from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from database import get_connection

from .config import AgentSettings, SYSTEM_PROMPT, TOOLS
from .state import AgentState, ToolCall, ToolObservation, VerificationResult


class ChatModel(Protocol):
    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> "ModelDecision":
        ...


@dataclass
class ModelDecision:
    message: dict[str, Any]
    tool_calls: list[ToolCall]
    final_text: str | None
    finish_reason: str
    usage: dict[str, Any] | None = None


class OpenAIChatModel:
    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        response = await asyncio.to_thread(
            self.client.chat.completions.create,
            model=self.model,
            max_tokens=2048,
            tools=tools,
            messages=messages,
        )
        choice = response.choices[0]
        msg = choice.message
        tool_calls = []
        raw_calls = getattr(msg, "tool_calls", None) or []
        for tc in raw_calls:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid_json": tc.function.arguments}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        message = {"role": "assistant", "content": msg.content}
        if tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in tool_calls
            ]

        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        return ModelDecision(
            message=message,
            tool_calls=tool_calls,
            final_text=msg.content,
            finish_reason=choice.finish_reason,
            usage=usage,
        )


class ContextManager:
    def build(self, state: AgentState, strategy_name: str) -> list[dict[str, Any]]:
        strategy_note = {
            "basic": "Gunakan pola Reason-Act-Observe sampai cukup untuk menjawab.",
            "plan_first": "Buat rencana singkat dulu sebelum memakai tool. Replan jika observasi tidak cocok.",
            "verify_repair": "Setelah aksi penting, gunakan observasi verifikasi. Jika gagal, perbaiki atau jelaskan kegagalan.",
        }.get(strategy_name, "")
        system = SYSTEM_PROMPT + f"\n\nNama pelanggan saat ini: {state.customer_name}\nStrategi loop: {strategy_name}. {strategy_note}"
        return [{"role": "system", "content": system}] + state.messages


class StateManager:
    def create(self, user_goal: str, customer_name: str, messages: list[dict[str, Any]]) -> AgentState:
        state = AgentState(user_goal=user_goal, customer_name=customer_name, messages=list(messages))
        state.record("run_started", task_id=state.task_id, user_goal=user_goal, customer_name=customer_name)
        return state


@dataclass
class PermissionDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = "allowed"


class PermissionManager:
    read_only_tools = {"check_menu", "check_stock", "get_order_status"}
    side_effect_tools = {"place_order"}

    def __init__(self, require_order_approval: bool = False) -> None:
        self.require_order_approval = require_order_approval

    def check(self, tool_call: ToolCall, approvals: set[str] | None = None) -> PermissionDecision:
        approvals = approvals or set()
        if tool_call.name in self.read_only_tools:
            return PermissionDecision(allowed=True)
        if tool_call.name in self.side_effect_tools:
            if self.require_order_approval and tool_call.id not in approvals and tool_call.name not in approvals:
                return PermissionDecision(False, True, "human approval required")
            return PermissionDecision(allowed=True)
        return PermissionDecision(False, False, f"tool '{tool_call.name}' is not registered")


class BudgetManager:
    def __init__(self, settings: AgentSettings) -> None:
        self.settings = settings

    def check_iteration(self, state: AgentState) -> str | None:
        if state.iteration >= self.settings.max_iterations:
            return "iteration limit reached"
        if len(state.tool_calls) >= self.settings.max_tool_calls:
            return "tool call limit reached"
        return None

    def can_retry(self, state: AgentState, key: str) -> bool:
        return state.retry_counts.get(key, 0) < self.settings.max_retries

    def record_retry(self, state: AgentState, key: str) -> int:
        state.retry_counts[key] = state.retry_counts.get(key, 0) + 1
        return state.retry_counts[key]


class ToolExecutor:
    def __init__(self, timeout_seconds: float = 5) -> None:
        self.timeout_seconds = timeout_seconds

    async def execute(self, tool_call: ToolCall) -> ToolObservation:
        started = time.time()
        try:
            result_text = await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, tool_call.name, tool_call.arguments),
                timeout=self.timeout_seconds,
            )
            result = json.loads(result_text) if isinstance(result_text, str) else result_text
            success = not (isinstance(result, dict) and result.get("success") is False)
            return ToolObservation(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                success=success,
                result=result,
                latency_ms=round((time.time() - started) * 1000, 2),
            )
        except asyncio.TimeoutError:
            return ToolObservation(tool_call.id, tool_call.name, False, error="tool timeout", latency_ms=round((time.time() - started) * 1000, 2))
        except Exception as exc:
            return ToolObservation(tool_call.id, tool_call.name, False, error=str(exc), latency_ms=round((time.time() - started) * 1000, 2))

    def _execute_sync(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        import mcp_server

        tool_fn = getattr(mcp_server, tool_name, None)
        if tool_fn is None:
            raise ValueError(f"Tool {tool_name} not found")
        return tool_fn(**tool_input)


class Verifier:
    def verify(self, tool_call: ToolCall, observation: ToolObservation) -> VerificationResult | None:
        if tool_call.name != "place_order":
            return None
        if not observation.success or not observation.result:
            return VerificationResult(tool_call.id, tool_call.name, False, "tool did not return a successful order", {"observation": observation.result or observation.error})

        order_code = observation.result.get("order_code")
        if not order_code:
            return VerificationResult(tool_call.id, tool_call.name, False, "missing order_code in tool result")

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT order_code, customer_name, total_price, status, items FROM orders WHERE order_code = ?", (order_code,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return VerificationResult(tool_call.id, tool_call.name, False, "order was not found in database", {"order_code": order_code})

        evidence = {
            "order_code": row["order_code"],
            "customer_name": row["customer_name"],
            "total_price": row["total_price"],
            "status": row["status"],
            "item_count": len(json.loads(row["items"])),
        }
        if row["status"] not in {"processing", "preparing", "ready", "completed"}:
            return VerificationResult(tool_call.id, tool_call.name, False, "order status is not valid", evidence)
        return VerificationResult(tool_call.id, tool_call.name, True, "order exists in database with valid status", evidence)


class Observability:
    def trace_summary(self, state: AgentState) -> dict[str, Any]:
        return {
            "task_id": state.task_id,
            "status": state.status,
            "stopping_reason": state.stopping_reason,
            "iterations": state.iteration,
            "tool_calls": len(state.tool_calls),
            "errors": len(state.errors),
            "verification_failures": len([v for v in state.verification_results if not v.passed]),
            "latency_ms": state.latency_ms,
            "events": [{"event": e.event, "timestamp": e.timestamp, "data": e.data} for e in state.trace],
        }


def append_tool_observation_message(state: AgentState, observation: ToolObservation, verification: VerificationResult | None = None) -> None:
    payload = {
        "tool_result": observation.result,
        "tool_success": observation.success,
        "tool_error": observation.error,
    }
    if verification:
        payload["verification"] = {
            "passed": verification.passed,
            "reason": verification.reason,
            "evidence": verification.evidence,
        }
    state.messages.append(
        {
            "role": "tool",
            "tool_call_id": observation.tool_call_id,
            "content": json.dumps(payload, ensure_ascii=False),
        }
    )


__all__ = [
    "TOOLS",
    "BudgetManager",
    "ChatModel",
    "ContextManager",
    "ModelDecision",
    "Observability",
    "OpenAIChatModel",
    "PermissionDecision",
    "PermissionManager",
    "StateManager",
    "ToolExecutor",
    "Verifier",
    "append_tool_observation_message",
]

