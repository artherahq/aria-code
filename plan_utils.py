"""
plan_utils.py — Plan parsing helpers for Aria Code CLI workflow commands.

Supports several natural input styles:

  Numbered steps (most common):
      1. Fetch AAPL quote
      2. Generate 6-month chart
      3. Output analysis report

  Bullet list:
      - Fetch quote
      - Generate chart
      - Output report

  Inline arrow / semicolon chain:
      fetch quote -> generate chart -> output report
      fetch quote; generate chart; output report

  Mixed (numbered + description):
      Step 1: Fetch AAPL quote
      Step 2: Generate chart with SMA20
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PlanStep:
    """A single executable step in a plan."""
    index:       int                   # 1-based position
    description: str                   # human-readable description
    name:        Optional[str] = None  # optional short name / label
    deps:        List[int] = field(default_factory=list)  # dependency indices

    def __str__(self) -> str:
        dep_str = f" [deps: {','.join(str(d) for d in self.deps)}]" if self.deps else ""
        return f"{self.index}. {self.description}{dep_str}"


# ── Patterns ──────────────────────────────────────────────────────────────────

# "1. text", "1) text", "Step 1: text", "Step 1 — text"
_RE_NUMBERED = re.compile(
    r"^(?:step\s*)?(\d+)[.):\-–—]\s*(.+)$",
    re.IGNORECASE,
)

# "- text", "• text", "* text", "· text"
_RE_BULLET = re.compile(r"^[-•*·]\s+(.+)$")

# Metadata tags like [name: Build] or [deps: 1,3]
_RE_META_NAME = re.compile(r"\[name:\s*([^\]]+)\]", re.IGNORECASE)
_RE_META_DEPS = re.compile(r"\[deps:\s*([^\]]+)\]",  re.IGNORECASE)


def _strip_meta(text: str) -> tuple[str, Optional[str], List[int]]:
    """Extract and remove [name:...] and [deps:...] metadata from text."""
    name: Optional[str] = None
    deps: List[int] = []

    m = _RE_META_NAME.search(text)
    if m:
        name = m.group(1).strip()
        text = text[:m.start()] + text[m.end():]

    m = _RE_META_DEPS.search(text)
    if m:
        raw = m.group(1)
        deps = [int(x.strip()) for x in re.split(r"[,;\s]+", raw) if x.strip().isdigit()]
        text = text[:m.start()] + text[m.end():]

    return text.strip(), name, deps


# ── Public API ────────────────────────────────────────────────────────────────

def parse_plan_steps(raw: str) -> List[str]:
    """
    Parse '/plan' argument string into a list of plain step description strings.

    This is the backwards-compatible API used by aria_cli.py.

    Returns a list of non-empty step strings, in order.
    """
    return [s.description for s in parse_plan(raw)]


def parse_plan(raw: str) -> List[PlanStep]:
    """
    Full parser — returns a list of PlanStep objects with index, description,
    optional name, and dependency list.

    Handles mixed input styles (numbered, bulleted, arrow/semicolon chained).
    """
    if not raw or not raw.strip():
        return []

    text = raw.strip()

    # ── Strategy 1: multiline numbered or bulleted steps ─────────────────────
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) >= 2:
        steps = _parse_lines(lines)
        if steps:
            return steps

    # ── Strategy 2: inline arrow chain  fetch quote -> chart -> report ───────
    # Also handles semicolons within arrow-separated parts:
    #   "git status -> rg TODO . ; pytest -q"  →  3 steps
    if "->" in text:
        arrow_parts = [p.strip() for p in text.replace("→", "->").split("->") if p.strip()]
        parts: List[str] = []
        for ap in arrow_parts:
            if ";" in ap:
                parts.extend(p.strip() for p in ap.split(";") if p.strip())
            else:
                parts.append(ap)
        return [PlanStep(index=i + 1, description=p) for i, p in enumerate(parts)]

    # ── Strategy 3: semicolon-separated steps ────────────────────────────────
    if ";" in text:
        parts = [p.strip() for p in text.split(";") if p.strip()]
        return [PlanStep(index=i + 1, description=p) for i, p in enumerate(parts)]

    # ── Strategy 4: single step ───────────────────────────────────────────────
    desc, name, deps = _strip_meta(text)
    return [PlanStep(index=1, description=desc, name=name, deps=deps)] if desc else []


def _parse_lines(lines: List[str]) -> List[PlanStep]:
    """Try to extract ordered steps from a list of text lines."""
    steps: List[PlanStep] = []
    expected_idx = 1

    for line in lines:
        # Try numbered pattern
        m = _RE_NUMBERED.match(line)
        if m:
            idx = int(m.group(1))
            desc_raw = m.group(2).strip()
            desc, name, deps = _strip_meta(desc_raw)
            if desc:
                steps.append(PlanStep(index=idx, description=desc, name=name, deps=deps))
            expected_idx = idx + 1
            continue

        # Try bullet pattern
        m = _RE_BULLET.match(line)
        if m:
            desc_raw = m.group(1).strip()
            desc, name, deps = _strip_meta(desc_raw)
            if desc:
                steps.append(PlanStep(index=expected_idx, description=desc, name=name, deps=deps))
                expected_idx += 1
            continue

    return steps


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_plan(steps: List[PlanStep], title: str = "Plan") -> str:
    """Format a plan for terminal display."""
    if not steps:
        return f"[{title}] (empty)"
    lines = [f"── {title} ({len(steps)} steps) ──"]
    for s in steps:
        dep_str = f"  (after {', '.join(str(d) for d in s.deps)})" if s.deps else ""
        label   = f" [{s.name}]" if s.name else ""
        lines.append(f"  {s.index}.{label} {s.description}{dep_str}")
    return "\n".join(lines)


def steps_to_prompt(steps: List[PlanStep], context: str = "") -> str:
    """
    Convert a list of PlanStep objects to a structured prompt string
    that the AI can execute step-by-step.
    """
    intro = f"{context}\n\n" if context else ""
    numbered = "\n".join(f"{s.index}. {s.description}" for s in steps)
    return (
        f"{intro}"
        f"Execute the following plan steps in order:\n\n"
        f"{numbered}\n\n"
        "Complete each step fully before moving to the next. "
        "After all steps, provide a brief summary of what was accomplished."
    )
