"""Approval decisions for tools that require user consent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalDecision:
    """Structured result from a tool approval prompt."""

    approved: bool
    policy: str | None = None
    user_approved: bool = False
    upgrade_policy: bool = False
    auto_approve_session: bool = False
    reason: str = ""

    @classmethod
    def allow(
        cls,
        *,
        policy: str | None = None,
        user_approved: bool = False,
        upgrade_policy: bool = False,
        auto_approve_session: bool = False,
        reason: str = "",
    ) -> "ApprovalDecision":
        return cls(
            approved=True,
            policy=policy,
            user_approved=user_approved,
            upgrade_policy=upgrade_policy,
            auto_approve_session=auto_approve_session,
            reason=reason,
        )

    @classmethod
    def deny(cls, reason: str = "") -> "ApprovalDecision":
        return cls(approved=False, reason=reason)


def apply_approval_decision(params: dict, decision: ApprovalDecision) -> dict:
    """Apply execution-facing approval fields to tool params."""
    if decision.policy is not None:
        params["policy"] = decision.policy
    if decision.user_approved:
        params["user_approved"] = True
    if decision.upgrade_policy:
        params["_upgrade_policy"] = True
    return params
