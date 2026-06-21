"""Typed public objects for the lightweight Aria Agent SDK."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class AriaAgentOptions:
    """Options for an Aria SDK agent session.

    The fields mirror the concerns exposed by modern agent SDKs: model/provider
    selection, deterministic tool routing, permission intent, cwd, and metadata.
    CLI rendering and terminal state stay outside this contract.
    """

    model: str = "qwen2.5-coder:1.5b"
    provider: str = "auto"
    ollama_url: str = "http://localhost:11434"
    api_url: str = "http://localhost:8000"
    auth_token: str = ""
    thinking_mode: str = "auto"
    user_context: dict[str, Any] = field(default_factory=dict)
    local_mode: bool = True
    deterministic: bool = True
    model_has_tools: bool = False
    system_prompt: str = ""
    permission_mode: str = "workspace-write"
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    max_turns: int = 1
    cwd: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    has_brokers: bool = False
    get_broker_registry: Callable[[], Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["get_broker_registry"] = None
        return data


@dataclass(frozen=True)
class AriaMessage:
    """Event/message yielded by the SDK query stream."""

    kind: str
    content: str = ""
    role: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "role": self.role,
            "content": self.content,
            "data": dict(self.data),
        }


@dataclass(frozen=True)
class AriaResult:
    """Final collected SDK result."""

    success: bool
    content: str = ""
    provider: str = ""
    session_id: str = ""
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "AriaAgentOptions",
    "AriaMessage",
    "AriaResult",
]
