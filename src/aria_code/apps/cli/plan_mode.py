"""Interactive plan mode — intercept tool calls for step-by-step approval.

When plan mode is active, the agent loop asks the user to approve each tool
call before execution. A plan-mode header shows which step is being proposed.

Usage (from aria_cli.py):
    from apps.cli.plan_mode import PlanModeState

    _PLAN = PlanModeState()          # module-level singleton

    # Enter plan mode:
    _PLAN.enter()

    # In _confirm_tool_execution_decision:
    if _PLAN.active and tool_name not in _CONFIRM_TOOLS:
        return _PLAN.confirm_step(tool_name, params, console, HAS_RICH)

    # Exit:
    _PLAN.exit()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PlanStep:
    index: int
    tool: str
    params: dict
    approved: Optional[bool] = None
    skipped: bool = False


@dataclass
class PlanModeState:
    active: bool = False
    _steps: List[PlanStep] = field(default_factory=list)
    _index: int = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def enter(self) -> None:
        self.active = True
        self._steps = []
        self._index = 0

    def exit(self) -> None:
        self.active = False
        self._steps = []
        self._index = 0

    # ── Step tracking ─────────────────────────────────────────────────────────

    def _next_index(self) -> int:
        self._index += 1
        return self._index

    def record_step(self, tool: str, params: dict, *, approved: bool) -> PlanStep:
        step = PlanStep(index=self._next_index(), tool=tool, params=params, approved=approved)
        self._steps.append(step)
        return step

    def summary(self) -> dict:
        total = len(self._steps)
        approved = sum(1 for s in self._steps if s.approved is True)
        rejected = sum(1 for s in self._steps if s.approved is False)
        return {"total": total, "approved": approved, "rejected": rejected}

    # ── Confirmation UI ───────────────────────────────────────────────────────

    def confirm_step(
        self,
        tool_name: str,
        params: dict,
        *,
        console,
        has_rich: bool,
        arrow_select_fn,
    ):
        """Show plan-mode confirmation prompt. Returns ApprovalDecision."""
        from runtime.approval import ApprovalDecision

        step_num = self._index + 1  # preview next number

        if has_rich and console:
            _param_preview = _format_params(params)
            console.print(
                f"\n  [bold cyan]◆ Plan Mode[/bold cyan]  "
                f"[dim]Step {step_num} — tool:[/dim] [bold]{tool_name}[/bold]"
            )
            if _param_preview:
                console.print(f"  [dim]{_param_preview}[/dim]")
        else:
            print(f"\n  [Plan Mode] Step {step_num}: {tool_name}")

        options = [
            ("Execute this step",        "执行此工具调用"),
            ("Skip this step",           "跳过，继续下一步"),
            ("Execute all remaining",    "从此步开始自动执行所有后续工具"),
            ("Abort plan",               "取消整个任务"),
        ]

        choice = arrow_select_fn(options, selected=0, title="")

        if choice == 0:
            self.record_step(tool_name, params, approved=True)
            return ApprovalDecision.allow()
        elif choice == 1:
            self.record_step(tool_name, params, approved=False)
            return ApprovalDecision.deny("skipped in plan mode")
        elif choice == 2:
            self.record_step(tool_name, params, approved=True)
            self.exit()  # turn off plan mode so remaining tools auto-execute
            return ApprovalDecision.allow(auto_approve_session=True)
        else:
            self.record_step(tool_name, params, approved=False)
            self.exit()
            return ApprovalDecision.deny("plan aborted by user")


def _format_params(params: dict) -> str:
    """Return a short one-line preview of tool params."""
    parts = []
    for key in ("path", "command", "url", "query", "symbol", "action"):
        if key in params:
            val = str(params[key])
            if len(val) > 80:
                val = val[:77] + "…"
            parts.append(f"{key}={val!r}")
    if not parts and params:
        first_key = next(iter(params))
        val = str(params[first_key])
        if len(val) > 80:
            val = val[:77] + "…"
        parts.append(f"{first_key}={val!r}")
    return "  ".join(parts)
