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


class RuntimeTrace:
    """In-memory trace of runtime events for session replay/debugging."""

    def __init__(self) -> None:
        self.events: List[RuntimeEvent] = []
        self.tool_calls: List[ToolCallRecord] = []

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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "events": [event.to_dict() for event in self.events],
            "tool_calls": [call.to_dict() for call in self.tool_calls],
        }
