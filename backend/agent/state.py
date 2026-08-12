from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal


AgentStatus = Literal["running", "waiting_for_approval", "completed", "failed"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolObservation:
    tool_call_id: str
    tool_name: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = 0
    retry_count: int = 0


@dataclass
class VerificationResult:
    tool_call_id: str
    tool_name: str
    passed: bool
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceEvent:
    event: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentState:
    user_goal: str
    customer_name: str
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: AgentStatus = "running"
    iteration: int = 0
    messages: list[dict[str, Any]] = field(default_factory=list)
    plan: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    observations: list[ToolObservation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    final_result: dict[str, Any] | None = None
    stopping_reason: str | None = None
    trace: list[TraceEvent] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    retry_counts: dict[str, int] = field(default_factory=dict)

    def record(self, event: str, **data: Any) -> None:
        self.trace.append(TraceEvent(event=event, data=_redact(data)))

    def finish(self, status: AgentStatus, reason: str, result: dict[str, Any] | None = None) -> None:
        self.status = status
        self.stopping_reason = reason
        self.final_result = result
        self.finished_at = time.time()
        self.record("run_finished", status=status, reason=reason)

    @property
    def latency_ms(self) -> float:
        end = self.finished_at or time.time()
        return round((end - self.created_at) * 1000, 2)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(secret in key.lower() for secret in ("key", "token", "secret", "password")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

