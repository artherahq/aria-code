"""Session export helpers for Aria Code."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Sequence

from artifacts import artifact_summary as build_artifact_summary
from apps.cli.config_paths import config_snapshot
from packages.aria_core import build_session_diagnostic_bundle


def _safe_title_from_conversation(conversation: Sequence[dict]) -> str:
    for msg in conversation:
        if msg.get("role") == "user":
            return str(msg.get("content", ""))[:60]
    return "Aria Code Session"


def build_session_export_payload(
    fmt: str,
    conversation: Sequence[dict],
    *,
    session_id: str = "",
    config: Optional[dict] = None,
    paths: Optional[dict] = None,
    trace: Any = None,
    provider_health: Optional[list] = None,
) -> tuple[str, str, str]:
    """Return (content, extension, suggested_filename_prefix)."""
    fmt = (fmt or "json").lower().strip()

    if fmt == "json":
        content = json.dumps(list(conversation), indent=2, ensure_ascii=False)
        return content, "json", "aria_code_chat"

    if fmt == "csv":
        lines = ["role,content"]
        for msg in conversation:
            escaped = str(msg.get("content", "")).replace('"', '""').replace("\n", " ")
            lines.append(f'{msg.get("role", "user")},"{escaped}"')
        return "\n".join(lines), "csv", "aria_code_chat"

    if fmt == "md":
        lines = [f"# Aria Code Chat Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
        for msg in conversation:
            prefix = "**You:**" if msg.get("role") == "user" else "**Aria:**"
            lines.append(f"{prefix}\n{msg.get('content', '')}\n")
        return "\n".join(lines), "md", "aria_code_chat"

    if fmt == "sft":
        pairs = []
        conv = list(conversation)
        i = 0
        while i < len(conv) - 1:
            if conv[i].get("role") == "user" and conv[i + 1].get("role") == "assistant":
                user_text = str(conv[i].get("content", "")).strip()
                assistant_text = str(conv[i + 1].get("content", "")).strip()
                if len(user_text) > 10 and len(assistant_text) > 20 and not user_text.startswith("Tool results:"):
                    pairs.append({
                        "instruction": user_text,
                        "input": "",
                        "output": assistant_text,
                        "source": "aria_cli_export",
                        "timestamp": datetime.now().strftime("%Y-%m-%d"),
                    })
                i += 2
            else:
                i += 1
        if not pairs:
            raise ValueError("No user→assistant pairs to export")
        return json.dumps(pairs, indent=2, ensure_ascii=False), "json", "aria_sft"

    if fmt == "bundle":
        bundle = build_session_diagnostic_bundle(
            session_id=session_id,
            conversation=conversation,
            config=config,
            paths=paths or config_snapshot(),
            trace=trace,
            provider_health=provider_health,
            artifact_summary=build_artifact_summary(),
        )
        return json.dumps(bundle, indent=2, ensure_ascii=False), "json", "aria_bundle"

    raise ValueError("Unsupported export format")
