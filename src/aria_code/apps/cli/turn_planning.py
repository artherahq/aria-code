"""Per-turn planning DECISIONS for send_message (pure, testable).

Extracted from ``aria_cli.send_message`` — the first slice of the file's
1300+ line stateful method to be pulled out. This module holds only the
*decision* logic (round budget, whether to trigger AI decomposition), which
depends solely on the user's message text and has no I/O, no ``self`` state,
and no side effects. It is safe to unit-test in isolation and safe to extract
without any behavior change, unlike the surrounding streaming/tool-loop code.

Mirrors send_message's inline logic exactly:
  • task complexity  → message > 120 chars, or contains a multi-step keyword
  • round budget      → complex tasks get a larger soft/hard round budget
  • decomposition     → long (>150 char), complex, non-slash-command messages
                         trigger an upfront AI decomposition pass
"""

from __future__ import annotations

TASK_COMPLEXITY_LENGTH_THRESHOLD = 120

TASK_COMPLEXITY_KEYWORDS = (
    "然后", "接着", "最后", "步骤", "并且", "同时",
    "and then", "step", "finally", "after that", "next",
    "完整", "全面", "详细", "系统", "comprehensive", "complete",
)

DECOMP_THRESHOLD = 150   # chars

SOFT_ROUND_BUDGET_SIMPLE = 16
SOFT_ROUND_BUDGET_COMPLEX = 30
HARD_ROUND_EXTENSION_SIMPLE = 10
HARD_ROUND_EXTENSION_COMPLEX = 20


def is_complex_task(message: str) -> bool:
    """A message is 'complex' if it's long or names a multi-step structure."""
    return (
        len(message) > TASK_COMPLEXITY_LENGTH_THRESHOLD
        or any(kw in message for kw in TASK_COMPLEXITY_KEYWORDS)
    )


def round_budget_for(is_complex: bool) -> tuple[int, int]:
    """-> (max_rounds, hard_max_rounds). Complex tasks get a larger budget
    on both the soft limit (where we start checking for real progress) and
    the hard extension (how far a still-progressing task can run past it)."""
    max_rounds = SOFT_ROUND_BUDGET_COMPLEX if is_complex else SOFT_ROUND_BUDGET_SIMPLE
    hard_max_rounds = max_rounds + (
        HARD_ROUND_EXTENSION_COMPLEX if is_complex else HARD_ROUND_EXTENSION_SIMPLE
    )
    return max_rounds, hard_max_rounds


def should_decompose(message: str, is_complex: bool) -> bool:
    """Trigger an upfront AI decomposition pass for long, complex, non-slash
    messages. Slash commands (``/...``) and bang commands (``!...``) are never
    decomposed — they're already a single deterministic action."""
    return (
        len(message) > DECOMP_THRESHOLD
        and is_complex
        and not any(message.startswith(p) for p in ("/", "!"))
    )
