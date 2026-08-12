from __future__ import annotations

import asyncio
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any

import database
from database import init_db

from .components import ModelDecision, PermissionManager, ToolExecutor, Verifier
from .config import AgentSettings
from .harness import AgentHarness, AgentTask
from .state import ToolCall, ToolObservation, VerificationResult


@dataclass
class Scenario:
    name: str
    description: str
    messages: list[dict[str, str]]
    model: "ScriptedModel"
    settings: AgentSettings
    permission_manager: PermissionManager | None = None
    tool_executor: ToolExecutor | None = None
    verifier: Verifier | None = None
    expected_statuses: set[str] | None = None


class ScriptedModel:
    def __init__(self, decisions: list[ModelDecision], repeat_last: bool = False) -> None:
        self.decisions = decisions
        self.repeat_last = repeat_last
        self.index = 0

    async def decide(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelDecision:
        if self.index >= len(self.decisions):
            if self.repeat_last and self.decisions:
                return self.decisions[-1]
            return final_decision("Baik kak, proses selesai.")
        decision = self.decisions[self.index]
        self.index += 1
        return decision


class FailingToolExecutor(ToolExecutor):
    async def execute(self, tool_call: ToolCall) -> ToolObservation:
        return ToolObservation(tool_call.id, tool_call.name, False, error="simulated tool failure")


class SlowToolExecutor(ToolExecutor):
    async def execute(self, tool_call: ToolCall) -> ToolObservation:
        await asyncio.sleep(0.02)
        return ToolObservation(tool_call.id, tool_call.name, False, error="tool timeout", latency_ms=20)


class FailingVerifier(Verifier):
    def verify(self, tool_call: ToolCall, observation: ToolObservation) -> VerificationResult | None:
        if tool_call.name == "place_order":
            return VerificationResult(tool_call.id, tool_call.name, False, "simulated verification failure", {"order_code": (observation.result or {}).get("order_code")})
        return None


def tool_decision(tool_id: str, name: str, arguments: dict[str, Any]) -> ModelDecision:
    call = ToolCall(tool_id, name, arguments)
    return ModelDecision(
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": str(call.arguments)},
                }
            ],
        },
        tool_calls=[call],
        final_text=None,
        finish_reason="tool_calls",
    )


def final_decision(text: str) -> ModelDecision:
    return ModelDecision(message={"role": "assistant", "content": text}, tool_calls=[], final_text=text, finish_reason="stop")


def build_scenarios() -> list[Scenario]:
    normal_settings = AgentSettings(max_iterations=6, max_tool_calls=8, max_retries=1, tool_timeout_seconds=5)
    low_limit_settings = AgentSettings(max_iterations=2, max_tool_calls=8, max_retries=0, tool_timeout_seconds=5)
    approval_settings = AgentSettings(max_iterations=4, max_tool_calls=4, max_retries=0, tool_timeout_seconds=5, require_order_approval=True)

    order_item = {"id": 1, "name": "Burger Klasik", "qty": 1, "price": 25000}
    return [
        Scenario(
            "successful_order",
            "Place a valid order and verify it exists.",
            [{"role": "user", "content": "Mau 1 Burger Klasik"}],
            ScriptedModel([tool_decision("tc1", "place_order", {"items": [order_item], "customer_name": "Budi"}), final_decision("Pesanan berhasil.")]),
            normal_settings,
            expected_statuses={"completed"},
        ),
        Scenario(
            "item_unavailable",
            "Ask for a menu item that does not exist.",
            [{"role": "user", "content": "Mau sushi"}],
            ScriptedModel([tool_decision("tc1", "check_stock", {"item_names": ["sushi"]}), final_decision("Menu sushi tidak tersedia.")]),
            normal_settings,
            expected_statuses={"completed"},
        ),
        Scenario(
            "insufficient_stock",
            "Request more quantity than stock allows.",
            [{"role": "user", "content": "Mau 99 Burger Double"}],
            ScriptedModel([tool_decision("tc1", "place_order", {"items": [{"id": 3, "name": "Burger Double", "qty": 99, "price": 35000}], "customer_name": "Budi"}), final_decision("Stok tidak cukup.")]),
            normal_settings,
            expected_statuses={"completed", "failed"},
        ),
        Scenario(
            "invalid_tool_arguments",
            "Model emits malformed place_order arguments.",
            [{"role": "user", "content": "Pesan item invalid"}],
            ScriptedModel([tool_decision("tc1", "place_order", {"items": [{"name": "Burger Klasik"}]}), final_decision("Data pesanan tidak valid.")]),
            normal_settings,
            expected_statuses={"completed", "failed"},
        ),
        Scenario(
            "tool_failure",
            "Tool executor returns a deterministic failure.",
            [{"role": "user", "content": "Mau menu"}],
            ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"}), final_decision("Tool gagal.")]),
            normal_settings,
            tool_executor=FailingToolExecutor(),
            expected_statuses={"completed", "failed"},
        ),
        Scenario(
            "tool_timeout",
            "Tool executor reports timeout.",
            [{"role": "user", "content": "Mau menu"}],
            ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"}), final_decision("Tool timeout.")]),
            normal_settings,
            tool_executor=SlowToolExecutor(timeout_seconds=0.001),
            expected_statuses={"completed", "failed"},
        ),
        Scenario(
            "verification_failure",
            "place_order succeeds but verifier rejects the action.",
            [{"role": "user", "content": "Mau 1 Burger Klasik"}],
            ScriptedModel([tool_decision("tc1", "place_order", {"items": [order_item], "customer_name": "Budi"}), final_decision("Tidak bisa diverifikasi.")]),
            normal_settings,
            verifier=FailingVerifier(),
            expected_statuses={"completed", "failed"},
        ),
        Scenario(
            "user_changes_request",
            "Conversation asks for one item, then changes to another.",
            [{"role": "user", "content": "Mau Burger Klasik"}, {"role": "user", "content": "Ganti jadi Ayam Goreng"}],
            ScriptedModel([tool_decision("tc1", "check_stock", {"item_names": ["Ayam Goreng"]}), final_decision("Baik, Ayam Goreng tersedia.")]),
            normal_settings,
            expected_statuses={"completed"},
        ),
        Scenario(
            "iteration_limit_reached",
            "Model keeps asking for tools until the budget stops the run.",
            [{"role": "user", "content": "Loop terus"}],
            ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"})], repeat_last=True),
            low_limit_settings,
            expected_statuses={"failed"},
        ),
        Scenario(
            "permission_denied_approval_required",
            "place_order requires human approval and no approval is supplied.",
            [{"role": "user", "content": "Mau 1 Burger Klasik"}],
            ScriptedModel([tool_decision("tc1", "place_order", {"items": [order_item], "customer_name": "Budi"})]),
            approval_settings,
            permission_manager=PermissionManager(require_order_approval=True),
            expected_statuses={"waiting_for_approval"},
        ),
    ]


async def run_scenario(strategy: str, scenario: Scenario) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        database.DB_PATH = os.path.join(tmpdir, "drivethru_eval.db")
        init_db()
        import mcp_server  # noqa: F401 - warm MCP module outside measured loop latency

        started = time.time()
        harness = AgentHarness(
            scenario.model,
            settings=AgentSettings(
                max_iterations=scenario.settings.max_iterations,
                max_tool_calls=scenario.settings.max_tool_calls,
                max_retries=scenario.settings.max_retries,
                tool_timeout_seconds=scenario.settings.tool_timeout_seconds,
                require_order_approval=scenario.settings.require_order_approval,
                default_strategy=strategy,
            ),
            permission_manager=scenario.permission_manager,
            tool_executor=scenario.tool_executor,
            verifier=scenario.verifier,
        )
        result = await harness.run(AgentTask(scenario.messages, customer_name="Budi", strategy=strategy))
        trace = result["trace"]
        status = result["agent_status"]
        expected = scenario.expected_statuses or {"completed"}
        return {
            "scenario": scenario.name,
            "strategy": strategy,
            "passed": status in expected,
            "status": status,
            "iterations": trace["iterations"],
            "tool_calls": trace["tool_calls"],
            "retry_count": len([e for e in trace["events"] if e["event"] in {"retry_scheduled", "repair_requested"}]),
            "verification_failures": trace["verification_failures"],
            "latency_ms": round((time.time() - started) * 1000, 2),
        }


async def run_benchmark(strategies: list[str] | None = None) -> list[dict[str, Any]]:
    strategies = strategies or ["basic", "plan_first", "verify_repair"]
    results = []
    for strategy in strategies:
        for scenario in build_scenarios():
            results.append(await run_scenario(strategy, scenario))
    return results


def summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for strategy in sorted({r["strategy"] for r in results}):
        selected = [r for r in results if r["strategy"] == strategy]
        rows.append(
            {
                "Strategy": strategy,
                "Success": f"{sum(1 for r in selected if r['passed'])}/{len(selected)}",
                "Avg Steps": round(sum(r["iterations"] for r in selected) / len(selected), 2),
                "Avg Tools": round(sum(r["tool_calls"] for r in selected) / len(selected), 2),
                "Retries": sum(r["retry_count"] for r in selected),
                "Verification Failures": sum(r["verification_failures"] for r in selected),
                "Latency ms": round(sum(r["latency_ms"] for r in selected) / len(selected), 2),
            }
        )
    return rows
