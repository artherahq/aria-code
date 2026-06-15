"""Compatibility wrapper for Aria Code command safety APIs."""

from __future__ import annotations

from safety.permissions import (
    SAFE_POLICIES,
    PolicyDecision,
    classify_command_risk,
    evaluate_command_policy,
    normalize_command,
)

__all__ = [
    "SAFE_POLICIES",
    "PolicyDecision",
    "classify_command_risk",
    "evaluate_command_policy",
    "normalize_command",
]
