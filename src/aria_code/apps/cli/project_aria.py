"""
Project-level ARIA.md helpers.

Used by /init scaffolds and repository bootstrap to keep project memory files
structured and consistent.
"""
from __future__ import annotations


def build_project_aria_md(
    *,
    project_name: str,
    stack: str,
    entry: str = "main.py",
    purpose: str = "",
    notes: list[str] | None = None,
    conventions: list[str] | None = None,
) -> str:
    """Build a durable project-level ARIA.md template."""
    notes = notes or []
    conventions = conventions or [
        "Keep CLI output stable and avoid leaking local paths in normal UI.",
        "Prefer thin CLI entry points plus command mixins for implementation.",
        "Treat turn envelopes, traces, exports, and provider health as shared contracts.",
    ]

    lines = [
        "# Memory",
        "",
        f"- **Project**: {project_name}",
        f"- **Stack**: {stack}",
        f"- **Entry**: {entry}",
    ]
    if purpose:
        lines.append(f"- **Purpose**: {purpose}")
    lines.append("- **Conventions**:")
    lines.extend(f"  - {item}" for item in conventions)
    if notes:
        lines.append("- **Notes**:")
        lines.extend(f"  - {item}" for item in notes)

    lines.extend([
        "",
        "## Memory Layers",
        "- Project ARIA.md describes this repository and overrides the global profile when both apply.",
        "- `~/.arthera/ARIA.md` is the user profile and carries cross-project preferences.",
        "- Session history and traces stay ephemeral unless exported explicitly.",
        "",
        "## Operational Rules",
        "- Keep generated code and user artifacts in the user's workspace, not in the source tree, unless the user asks otherwise.",
        "- Prefer deterministic service output envelopes for CLI, trace, and export surfaces.",
        "- Use `/doctor` or the health views before trusting external providers.",
        "",
        "## Workflow Notes",
        "- Use `/init` to refresh project context after substantial structural changes.",
        "- Use `/memory add` for durable project facts and `/memory profile` for user preferences.",
        "- Keep long sessions compacted and avoid relying on chat history as the only source of truth.",
        "",
    ])
    return "\n".join(lines)
