"""Shared responsive layout primitives for structured terminal output."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from typing import Literal, Sequence


StructuredLayout = Literal["stacked", "compact", "full"]


@dataclass(frozen=True)
class StackedRecord:
    """One narrow-terminal record with a headline and readable detail rows."""

    headline: str
    lines: tuple[str, ...]


def terminal_width(console=None, *, fallback: int = 80) -> int:
    """Return a stable terminal width for real and recording consoles."""
    try:
        width = int(getattr(console, "width", 0) or 0)
        if width > 0:
            return width
    except (TypeError, ValueError):
        pass
    return int(shutil.get_terminal_size((fallback, 24)).columns or fallback)


def select_structured_layout(
    width: int,
    *,
    stacked_below: int = 96,
    full_at: int = 120,
) -> StructuredLayout:
    """Choose stacked cards, a reduced table, or the full table."""
    if width < stacked_below:
        return "stacked"
    if width < full_at:
        return "compact"
    return "full"


def structured_layout(
    console=None,
    *,
    width: int | None = None,
    stacked_below: int = 96,
    full_at: int = 120,
) -> StructuredLayout:
    return select_structured_layout(
        width if width is not None else terminal_width(console),
        stacked_below=stacked_below,
        full_at=full_at,
    )


def render_stacked_records(
    console,
    *,
    title: str,
    records: Sequence[StackedRecord],
    footer: str = "",
) -> None:
    """Render narrow structured data without a wide table or panel border."""
    console.print(f"[bold]{title}[/bold]")
    for index, record in enumerate(records, start=1):
        console.print(f"  [bold]#{index}  {record.headline}[/bold]")
        for line in record.lines:
            if str(line).strip():
                console.print(f"      {line}")
    if footer:
        console.print(f"  [dim]{footer}[/dim]")
