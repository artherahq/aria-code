"""Runtime event and trace records for Aria Code."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class RuntimeEvent:
    event_id: str
    type: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, event_type: str, data: Dict[str, Any] | None = None) -> "RuntimeEvent":
        return cls(uuid.uuid4().hex[:12], event_type, time.time(), data or {})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolCallRecord:
    tool: str
    params: Dict[str, Any]
    result: Dict[str, Any]
    elapsed_ms: float
    started_at: float
    ended_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TurnResultRecord:
    status: str
    success: bool
    cancelled: bool
    provider: str
    error: str
    final_text: str
    summary: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RuntimeTrace:
    """In-memory trace of runtime events for session replay/debugging."""

    def __init__(self) -> None:
        self.events: List[RuntimeEvent] = []
        self.tool_calls: List[ToolCallRecord] = []
        self.turn_results: List[TurnResultRecord] = []

    def emit(self, event_type: str, data: Dict[str, Any] | None = None) -> RuntimeEvent:
        event = RuntimeEvent.create(event_type, data)
        self.events.append(event)
        return event

    def add_tool_call(self, record: ToolCallRecord) -> None:
        self.tool_calls.append(record)
        self.emit("tool_result", {
            "tool": record.tool,
            "success": bool(record.result.get("success")),
            "elapsed_ms": record.elapsed_ms,
        })

    def add_turn_result(self, record: Dict[str, Any]) -> TurnResultRecord:
        turn = TurnResultRecord(
            status=str(record.get("status", "")),
            success=bool(record.get("success")),
            cancelled=bool(record.get("cancelled")),
            provider=str(record.get("provider", "")),
            error=str(record.get("error", "")),
            final_text=str(record.get("final_text", "")),
            summary=str(record.get("summary", "")),
            metadata=dict(record.get("metadata") or {}),
        )
        self.turn_results.append(turn)
        self.emit("turn_complete", {
            "status": turn.status,
            "success": turn.success,
            "cancelled": turn.cancelled,
            "provider": turn.provider,
            "summary": turn.summary,
        })
        return turn

    def to_dict(self) -> Dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "turn_results": [turn.to_dict() for turn in self.turn_results],
        }
