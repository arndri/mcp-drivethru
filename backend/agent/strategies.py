from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from .components import (
    BudgetManager,
    ChatModel,
    ContextManager,
    PermissionManager,
    ToolExecutor,
    Verifier,
    append_tool_observation_message,
)
from .config import TOOLS
from .state import AgentState, ToolCall, ToolObservation, VerificationResult


class LoopStrategy(ABC):
    name: str

    @abstractmethod
    async def run(self, state: AgentState, runtime: "LoopRuntime") -> AgentState:
        ...


class LoopRuntime:
    def __init__(
        self,
        model: ChatModel,
        context: ContextManager,
        permissions: PermissionManager,
        budget: BudgetManager,
        executor: ToolExecutor,
        verifier: Verifier,
        approvals: set[str] | None = None,
    ) -> None:
        self.model = model
        self.context = context
        self.permissions = permissions
        self.budget = budget
        self.executor = executor
        self.verifier = verifier
        self.approvals = approvals or set()

    async def model_decision(self, state: AgentState, strategy_name: str):
        context_messages = self.context.build(state, strategy_name)
        decision = await self.model.decide(context_messages, TOOLS)
        state.messages.append(decision.message)
        state.record(
            "model_decision",
            iteration=state.iteration,
            finish_reason=decision.finish_reason,
            tool_calls=[{"id": call.id, "name": call.name, "arguments": call.arguments} for call in decision.tool_calls],
            final_text=decision.final_text,
            usage=decision.usage,
        )
        return decision

    async def act_observe_verify(self, state: AgentState, tool_call: ToolCall, verify: bool) -> tuple[ToolObservation | None, VerificationResult | None]:
        permission = self.permissions.check(tool_call, self.approvals)
        state.record("permission_checked", tool=tool_call.name, allowed=permission.allowed, requires_approval=permission.requires_approval, reason=permission.reason)
        if permission.requires_approval:
            state.finish(
                "waiting_for_approval",
                "approval required",
                {
                    "reply": "Pesanan butuh konfirmasi dulu sebelum diproses.",
                    "approval_required": {"tool_call_id": tool_call.id, "tool": tool_call.name, "arguments": tool_call.arguments},
                    "order_info": None,
                },
            )
            return None, None
        if not permission.allowed:
            observation = ToolObservation(tool_call.id, tool_call.name, False, error=permission.reason)
            state.observations.append(observation)
            state.errors.append(permission.reason)
            append_tool_observation_message(state, observation)
            return observation, None

        state.tool_calls.append(tool_call)
        observation = await self.executor.execute(tool_call)
        state.observations.append(observation)
        state.record("tool_observed", tool=tool_call.name, success=observation.success, result=observation.result, error=observation.error, latency_ms=observation.latency_ms)

        verification = self.verifier.verify(tool_call, observation) if verify else None
        if verification:
            state.verification_results.append(verification)
            state.record("verification_result", tool=tool_call.name, passed=verification.passed, reason=verification.reason, evidence=verification.evidence)
            if not verification.passed:
                state.errors.append(verification.reason)

        append_tool_observation_message(state, observation, verification)
        return observation, verification


class BasicLoopStrategy(LoopStrategy):
    name = "basic"

    async def run(self, state: AgentState, runtime: LoopRuntime) -> AgentState:
        while state.status == "running":
            limit_reason = runtime.budget.check_iteration(state)
            if limit_reason:
                return _finish_limit(state, limit_reason)

            state.iteration += 1
            state.record("iteration_started", strategy=self.name, iteration=state.iteration)
            decision = await runtime.model_decision(state, self.name)

            if not decision.tool_calls:
                return _finish_success(state, decision.final_text)

            for tool_call in decision.tool_calls:
                observation, _ = await runtime.act_observe_verify(state, tool_call, verify=False)
                if state.status != "running":
                    return state
                if observation and not observation.success:
                    retry_key = f"{tool_call.name}:{observation.error}"
                    if runtime.budget.can_retry(state, retry_key):
                        retry_count = runtime.budget.record_retry(state, retry_key)
                        observation.retry_count = retry_count
                        state.record("retry_scheduled", tool=tool_call.name, retry_count=retry_count)
                    else:
                        state.record("retry_exhausted", tool=tool_call.name, error=observation.error)
        return state


class PlanFirstLoopStrategy(LoopStrategy):
    name = "plan_first"

    async def run(self, state: AgentState, runtime: LoopRuntime) -> AgentState:
        state.plan = [
            "Understand the customer goal",
            "Use menu and stock tools when needed",
            "Only place an order after enough item details are known",
            "Recover from failed observations before final response",
        ]
        state.record("plan_created", plan=state.plan)
        return await BasicLoopStrategy().run(state, runtime)


class VerifyRepairLoopStrategy(LoopStrategy):
    name = "verify_repair"

    async def run(self, state: AgentState, runtime: LoopRuntime) -> AgentState:
        while state.status == "running":
            limit_reason = runtime.budget.check_iteration(state)
            if limit_reason:
                return _finish_limit(state, limit_reason)

            state.iteration += 1
            state.record("iteration_started", strategy=self.name, iteration=state.iteration)
            decision = await runtime.model_decision(state, self.name)

            if not decision.tool_calls:
                return _finish_success(state, decision.final_text)

            for tool_call in decision.tool_calls:
                observation, verification = await runtime.act_observe_verify(state, tool_call, verify=True)
                if state.status != "running":
                    return state
                if _needs_repair(observation, verification):
                    reason = _repair_reason(observation, verification)
                    retry_key = f"{tool_call.name}:{reason}"
                    if runtime.budget.can_retry(state, retry_key):
                        retry_count = runtime.budget.record_retry(state, retry_key)
                        state.record("repair_requested", tool=tool_call.name, reason=reason, retry_count=retry_count)
                        state.messages.append(
                            {
                                "role": "system",
                                "content": f"OBSERVASI HARNESS: aksi {tool_call.name} gagal atau tidak terverifikasi: {reason}. Perbaiki rencana, jangan ulangi tanpa perubahan.",
                            }
                        )
                    else:
                        state.finish("failed", "retry limit reached", {"reply": "Maaf, pesanan belum bisa diproses karena verifikasi gagal.", "order_info": None})
                        return state
        return state


def get_strategy(name: str) -> LoopStrategy:
    strategies: dict[str, LoopStrategy] = {
        "basic": BasicLoopStrategy(),
        "plan_first": PlanFirstLoopStrategy(),
        "verify_repair": VerifyRepairLoopStrategy(),
    }
    if name not in strategies:
        raise ValueError(f"Unknown loop strategy '{name}'")
    return strategies[name]


def _finish_success(state: AgentState, final_text: str | None) -> AgentState:
    order_info = _latest_order_info(state)
    state.finish("completed", "model returned final response", {"reply": final_text or "", "order_info": order_info})
    return state


def _finish_limit(state: AgentState, reason: str) -> AgentState:
    state.finish("failed", reason, {"reply": "Maaf, proses pesanan melewati batas eksekusi. Silakan coba lagi ya kak!", "order_info": None})
    return state


def _latest_order_info(state: AgentState) -> dict[str, Any] | None:
    for observation in reversed(state.observations):
        data = observation.result or {}
        if data.get("success") and data.get("order_code"):
            return {
                "order_code": data["order_code"],
                "total_price": data["total_price"],
                "estimated_time": data.get("estimated_time"),
                "items": data.get("items", []),
            }
    return None


def _needs_repair(observation: ToolObservation | None, verification: VerificationResult | None) -> bool:
    if observation is None:
        return False
    if not observation.success:
        return True
    if verification is not None and not verification.passed:
        return True
    return False


def _repair_reason(observation: ToolObservation | None, verification: VerificationResult | None) -> str:
    if verification is not None and not verification.passed:
        return verification.reason
    if observation is not None:
        return observation.error or json.dumps(observation.result, ensure_ascii=False)
    return "unknown failure"

