from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .components import (
    BudgetManager,
    ChatModel,
    ContextManager,
    Observability,
    PermissionManager,
    StateManager,
    ToolExecutor,
    Verifier,
)
from .config import AgentSettings
from .state import AgentState
from .strategies import LoopRuntime, get_strategy


@dataclass
class AgentTask:
    messages: list[dict[str, Any]]
    customer_name: str
    strategy: str | None = None
    approvals: set[str] | None = None

    @property
    def user_goal(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""


class AgentHarness:
    def __init__(
        self,
        model: ChatModel,
        settings: AgentSettings | None = None,
        permission_manager: PermissionManager | None = None,
        tool_executor: ToolExecutor | None = None,
        verifier: Verifier | None = None,
        context_manager: ContextManager | None = None,
        state_manager: StateManager | None = None,
        observability: Observability | None = None,
    ) -> None:
        self.settings = settings or AgentSettings()
        self.model = model
        self.context = context_manager or ContextManager()
        self.state_manager = state_manager or StateManager()
        self.permissions = permission_manager or PermissionManager(self.settings.require_order_approval)
        self.budget = BudgetManager(self.settings)
        self.executor = tool_executor or ToolExecutor(self.settings.tool_timeout_seconds)
        self.verifier = verifier or Verifier()
        self.observability = observability or Observability()
        self.last_state: AgentState | None = None

    async def run(self, task: AgentTask) -> dict[str, Any]:
        final_state = await self.run_to_state(task)
        result = final_state.final_result or {"reply": "", "order_info": None}
        result.update(
            {
                "task_id": final_state.task_id,
                "agent_status": final_state.status,
                "stopping_reason": final_state.stopping_reason,
                "trace": self.observability.trace_summary(final_state),
            }
        )
        return result

    async def run_to_state(self, task: AgentTask) -> AgentState:
        strategy_name = task.strategy or self.settings.default_strategy
        state = self.state_manager.create(task.user_goal, task.customer_name, task.messages)
        runtime = LoopRuntime(
            model=self.model,
            context=self.context,
            permissions=self.permissions,
            budget=self.budget,
            executor=self.executor,
            verifier=self.verifier,
            approvals=task.approvals,
        )
        try:
            strategy = get_strategy(strategy_name)
            final_state = await strategy.run(state, runtime)
        except Exception as exc:
            state.errors.append(str(exc))
            state.finish("failed", "unhandled harness error", {"reply": "Maaf, ada gangguan teknis. Silakan coba lagi ya kak!", "order_info": None})
            final_state = state

        self.last_state = final_state
        return final_state
