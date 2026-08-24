"""Small, dependency-free input helpers for read-only warehouse agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def records(payload: Mapping[str, Any] | None, key: str) -> list[Mapping[str, Any]]:
    """Return only mapping records from an optional ERP response payload."""
    value = payload.get(key, []) if isinstance(payload, Mapping) else []
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
