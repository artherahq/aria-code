"""runtime/procedural_trace.py — turn local runtime records into the
cross-repo execution-trace contract Arthera's procedural memory expects.

Why this exists
----------------
`runtime/task_ledger.py`, `runtime/verify_loop.py` and `runtime/self_healing.py`
each produce a real record of "what was attempted and what happened" — exactly
the goal → steps → outcome shape a procedural-memory layer needs to learn from.
None of that ever left this process before: it lived in a local JSON file or a
dataclass returned to one caller, then was gone. This module is the thin
adapter layer that turns those native shapes into the JSON contract Arthera's
`packages/ml/llm/memory/procedural_memory.py` (`trace_from_contract`) expects,
plus the consent-gated wiring that actually sends them.

Cross-repo boundary is JSON only, never shared Python objects
---------------------------------------------------------------
Adapters here take and return plain dicts, not Arthera's `ExecutionTrace`
dataclass. This is deliberate: this same session already spent real time
tracking down a cross-repo Python import bridge that silently broke when
aria-code's own package layout changed underneath it. A dict contract crossing
a repo boundary degrades gracefully when one side changes shape (extra/missing
keys); a shared Python class does not.

Consent, not best-effort-by-default
-------------------------------------
`wire_trace_reporters()` only activates when the user has explicitly turned on
BOTH `data_sharing` and `feedback_upload` — the same pair every other upload
path in this CLI already requires (see `apps/cli/commands/core_cmds.py`,
`ui/banner.py`). Task prompts, code, and error output are exactly the kind of
content a user may not want leaving their machine; there is no such thing as
a safe default here, only an explicit one.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# ==================== Adapters (pure, no I/O) ====================


def trace_from_subagent_snapshot(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a contract dict from `SubagentTask.snapshot()`.

    Returns None for non-terminal states (pending/running/cancelled) — those
    do not yet say anything about how the task actually went.
    """
    status = snapshot.get("status")
    if status not in ("done", "failed"):
        return None

    error = snapshot.get("error") or ""
    steps: List[Dict[str, Any]] = [{
        "action": f"subagent_task:{snapshot.get('backend', 'aria')}",
        "observation": (snapshot.get("result") or "")[:2000],
        "error": error or None,
        "metadata": {
            "mode": snapshot.get("mode"),
            "isolation": snapshot.get("isolation"),
            "applied": bool(snapshot.get("applied")),
        },
    }]
    outcome = "success" if status == "done" and not error else "failure"

    return {
        "goal": snapshot.get("prompt", ""),
        "steps": steps,
        "outcome": outcome,
        "source": "aria_code.task_ledger",
        "source_ref": snapshot.get("task_id"),
        "session_id": snapshot.get("session_id") or None,
        "metadata": {"branch": snapshot.get("branch") or None},
    }


def trace_from_verification_summary(
    summary: Dict[str, Any],
    goal: str,
    source_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a contract dict from `VerificationSummary.to_dict()`.

    Reachable via `runtime/verify_loop.py`'s `tool_verify_changes()`, which
    wires this adapter to the reporter registered by `wire_trace_reporters()`.
    `tool_verify_changes` is a plain `params: dict -> dict` tool function
    (same contract as `subagent.py`'s `tool_spawn_task`) — whether the model
    can actually call it depends on it being registered in the CLI's tool
    schema, which is a separate step from this module.
    """
    steps: List[Dict[str, Any]] = []
    passed_any = False
    failed_any = False
    for check in summary.get("checks", []):
        passed = bool(check.get("passed"))
        passed_any = passed_any or passed
        failed_any = failed_any or not passed
        steps.append({
            "action": f"verify:{check.get('name')}",
            "observation": check.get("command", ""),
            "error": "; ".join(check.get("extracted_errors") or []) or None,
        })

    if summary.get("all_passed"):
        outcome = "success"
    elif passed_any and failed_any:
        outcome = "partial"
    else:
        outcome = "failure"

    return {
        "goal": goal,
        "steps": steps,
        "outcome": outcome,
        "source": "aria_code.verify_loop",
        "source_ref": source_ref,
    }


def trace_from_self_healing_result(
    result: Dict[str, Any],
    goal: str,
    source_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a contract dict from `SelfHealingResult.to_dict()`.

    This is the richest native source: `patches_applied` already carries a
    per-attempt record, so a success with `retries_used > 0` naturally becomes
    the `success_after_recovery` shape procedural memory looks for.
    """
    steps: List[Dict[str, Any]] = []
    for patch in result.get("patches_applied") or []:
        detail = ", ".join(f"{k}={v}" for k, v in patch.items() if k != "type")
        steps.append({
            "action": f"self_heal_patch:{patch.get('type', 'fix')}",
            "observation": detail,
            "error": None,
        })

    success = bool(result.get("success"))
    steps.append({
        "action": "execute_and_heal:final_attempt",
        "observation": (result.get("final_output") or "")[:2000] if success else "",
        "error": None if success else result.get("error"),
    })

    retries = int(result.get("retries_used") or 0)
    if success and retries > 0:
        outcome = "success_after_recovery"
    elif success:
        outcome = "success"
    else:
        outcome = "failure"

    return {
        "goal": goal,
        "steps": steps,
        "outcome": outcome,
        "source": "aria_code.self_healing",
        "source_ref": source_ref,
        "metadata": {
            "retries_used": retries,
            "artifact_paths": result.get("artifact_paths") or [],
        },
    }


# ==================== Consent-gated wiring ====================


def wire_trace_reporters(config: Dict[str, Any]) -> bool:
    """Register best-effort execution-trace reporting for subagent tasks and
    self-healing runs.

    No-ops entirely (returns False) unless the user has opted in via BOTH
    `data_sharing` and `feedback_upload`, and is logged in with a reachable
    backend. Every cross-module import here is lazy and wrapped, so calling
    this speculatively at CLI startup costs nothing for a user who never
    opts in — matching `cloud_memory.py`'s own "silent until configured"
    contract.

    Returns True if reporting was actually wired (useful for /doctor-style
    diagnostics without reaching into module internals).
    """
    try:
        from aria_code.privacy.feedback import PrivacySettings
    except Exception:
        return False

    settings = PrivacySettings.from_config(config)
    if not (settings.data_sharing and settings.feedback_upload):
        return False

    try:
        from aria_code.cloud_memory import client_from_config
    except Exception:
        return False

    client = client_from_config(config)
    if not client.available:
        return False

    try:
        from . import subagent as _subagent
        from . import verify_loop as _verify_loop
        from aria_code.agents.engineering import tester as _tester
    except Exception:
        return False

    def _report_subagent(snapshot: Dict[str, Any]) -> None:
        contract = trace_from_subagent_snapshot(snapshot)
        if contract:
            client.report_execution_trace(contract)

    def _report_self_healing(result: Dict[str, Any], goal: str) -> None:
        client.report_execution_trace(
            trace_from_self_healing_result(result, goal=goal)
        )

    def _report_verification(summary: Dict[str, Any], goal: str) -> None:
        client.report_execution_trace(
            trace_from_verification_summary(summary, goal=goal)
        )

    _subagent.set_trace_reporter(_report_subagent)
    _tester.set_trace_reporter(_report_self_healing)
    _verify_loop.set_trace_reporter(_report_verification)
    return True


__all__ = [
    "trace_from_subagent_snapshot",
    "trace_from_verification_summary",
    "trace_from_self_healing_result",
    "wire_trace_reporters",
]
