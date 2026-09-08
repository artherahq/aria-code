"""Decide when to nudge a model that stated an intent but called no tool.

Extracted from the Ollama tool loop so the decision is pure and testable.

The nudge is a blunt instrument — it injects "Output a <tool_call> NOW" as a
user turn — so it must only fire when the model really did promise an action it
failed to take.  The original word list mixed first-person commitments
("let me", "我会") with ordinary Chinese discourse connectives ("下面",
"接下来", "检查"), which appear in almost any Chinese prose answer.  A project
review written entirely in prose therefore tripped the nudge, and the follow-up
round echoed the imperative, leaving a stray "NOW" at the end of the answer.
"""

from __future__ import annotations

# First-person commitments to act.  Every entry must read as "I am about to do
# something", not merely as a section transition.
_ACTION_COMMITMENTS = (
    "let me ",
    "i will ",
    "i'll ",
    "let's ",
    "i am going to ",
    "i'm going to ",
    "让我来",
    "让我先",
    "我来试",
    "我会调用",
    "我将调用",
    "我来调用",
    "我现在就",
    "我这就",
)

# Deliberately NOT treated as intent: "接下来", "下面", "检查", "我需要",
# "再次", "重新", "fix", "retry", "check".  These are ordinary connectives and
# verbs that carry no promise of a tool call — they were the false positives.

MAX_NUDGES = 5


def looks_like_stated_intent(text: str) -> bool:
    """True when *text* promises an action the model did not take."""
    low = (text or "").strip().lower()
    if not low:
        return False
    return any(phrase in low for phrase in _ACTION_COMMITMENTS)


def should_nudge_for_action(
    text: str,
    *,
    in_error_recovery: bool = False,
    last_tool_had_error: bool = False,
    nudge_count: int = 0,
    tools_available: bool = True,
    max_nudges: int = MAX_NUDGES,
) -> bool:
    """Whether to push the model toward a tool call instead of accepting prose.

    Error recovery and a failed previous tool are strong signals and stand on
    their own.  A merely stated intent is a weak signal and additionally
    requires that tools exist to call: nudging a model that has no tools
    produces an imperative it can only echo.
    """
    if nudge_count >= max_nudges:
        return False
    if not tools_available:
        return False
    if in_error_recovery or last_tool_had_error:
        return True
    return looks_like_stated_intent(text)


__all__ = [
    "MAX_NUDGES",
    "looks_like_stated_intent",
    "should_nudge_for_action",
]
