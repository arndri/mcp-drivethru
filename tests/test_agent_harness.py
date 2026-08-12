import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import database
from database import init_db
from agent.components import PermissionManager
from agent.config import AgentSettings
from agent.evaluation import FailingToolExecutor, FailingVerifier, ScriptedModel, final_decision, tool_decision
from agent.harness import AgentHarness, AgentTask


def run(coro):
    return asyncio.run(coro)


class AgentHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        database.DB_PATH = os.path.join(self.tmpdir.name, "test.db")
        init_db()

    def tearDown(self):
        self.tmpdir.cleanup()

    def harness(self, model, **overrides):
        settings = overrides.pop("settings", AgentSettings(max_iterations=5, max_tool_calls=6, max_retries=1, tool_timeout_seconds=1))
        return AgentHarness(model, settings=settings, **overrides)

    def task(self, strategy="verify_repair"):
        return AgentTask([{"role": "user", "content": "Mau 1 Burger Klasik"}], "Budi", strategy=strategy)

    def test_state_transitions_to_completed(self):
        model = ScriptedModel([final_decision("Halo kak.")])
        state = run(self.harness(model).run_to_state(self.task()))
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.stopping_reason, "model returned final response")

    def test_iteration_limit_stops_loop(self):
        settings = AgentSettings(max_iterations=1, max_tool_calls=10, max_retries=0, tool_timeout_seconds=1)
        model = ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"})], repeat_last=True)
        state = run(self.harness(model, settings=settings).run_to_state(self.task("basic")))
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.stopping_reason, "iteration limit reached")

    def test_tool_call_limit_stops_loop(self):
        settings = AgentSettings(max_iterations=5, max_tool_calls=1, max_retries=0, tool_timeout_seconds=1)
        model = ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"})], repeat_last=True)
        state = run(self.harness(model, settings=settings).run_to_state(self.task("basic")))
        self.assertEqual(state.status, "failed")
        self.assertEqual(state.stopping_reason, "tool call limit reached")

    def test_permission_blocks_unknown_tool(self):
        model = ScriptedModel([tool_decision("tc1", "delete_database", {}), final_decision("Tidak boleh.")])
        state = run(self.harness(model).run_to_state(self.task("basic")))
        self.assertIn("not registered", state.observations[0].error)

    def test_place_order_can_wait_for_human_approval(self):
        settings = AgentSettings(max_iterations=5, max_tool_calls=6, max_retries=1, tool_timeout_seconds=1, require_order_approval=True)
        item = {"id": 1, "name": "Burger Klasik", "qty": 1, "price": 25000}
        model = ScriptedModel([tool_decision("tc1", "place_order", {"items": [item], "customer_name": "Budi"})])
        state = run(self.harness(model, settings=settings, permission_manager=PermissionManager(True)).run_to_state(self.task()))
        self.assertEqual(state.status, "waiting_for_approval")
        self.assertEqual(state.stopping_reason, "approval required")

    def test_place_order_executes_when_approved(self):
        settings = AgentSettings(max_iterations=5, max_tool_calls=6, max_retries=1, tool_timeout_seconds=1, require_order_approval=True)
        item = {"id": 1, "name": "Burger Klasik", "qty": 1, "price": 25000}
        model = ScriptedModel([tool_decision("tc1", "place_order", {"items": [item], "customer_name": "Budi"}), final_decision("Pesanan berhasil.")])
        state = run(self.harness(model, settings=settings, permission_manager=PermissionManager(True)).run_to_state(AgentTask(self.task().messages, "Budi", "verify_repair", approvals={"tc1"})))
        self.assertEqual(state.status, "completed")
        self.assertTrue(state.verification_results[-1].passed)

    def test_verification_failure_requests_repair(self):
        item = {"id": 1, "name": "Burger Klasik", "qty": 1, "price": 25000}
        model = ScriptedModel([tool_decision("tc1", "place_order", {"items": [item], "customer_name": "Budi"}), final_decision("Tidak bisa diverifikasi.")])
        state = run(self.harness(model, verifier=FailingVerifier()).run_to_state(self.task("verify_repair")))
        self.assertGreaterEqual(len([e for e in state.trace if e.event == "repair_requested"]), 1)

    def test_tool_failure_is_observable(self):
        model = ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"}), final_decision("Tool gagal.")])
        state = run(self.harness(model, tool_executor=FailingToolExecutor()).run_to_state(self.task("basic")))
        self.assertFalse(state.observations[0].success)
        self.assertEqual(state.observations[0].error, "simulated tool failure")

    def test_all_loop_strategies_run(self):
        for strategy in ("basic", "plan_first", "verify_repair"):
            with self.subTest(strategy=strategy):
                model = ScriptedModel([tool_decision("tc1", "check_menu", {"category": "all"}), final_decision("Menu tersedia.")])
                state = run(self.harness(model).run_to_state(self.task(strategy)))
                self.assertEqual(state.status, "completed")


if __name__ == "__main__":
    unittest.main()

