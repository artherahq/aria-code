"""
Context Engine for Aria Code Agent Runtime.

Provides budget-aware context assembly, ARIA.md/CLAUDE.md rule discovery,
codebase skeleton injection, and intelligent conversation compaction
mirroring Anthropic Claude Code and OpenAI Assistants runtime standards.
"""

from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Default token budgets for standard context window (assuming 128k/200k models)
DEFAULT_SYSTEM_BUDGET = 3000
DEFAULT_RULES_BUDGET = 4000
DEFAULT_REPOMAP_BUDGET = 4000
DEFAULT_HISTORY_BUDGET = 30000
DEFAULT_TOOL_RESULTS_BUDGET = 12000


@dataclass
class ContextBudgets:
    """Explicit token/character budget allocations across prompt segments."""
    system: int = DEFAULT_SYSTEM_BUDGET
    rules: int = DEFAULT_RULES_BUDGET
    repo_map: int = DEFAULT_REPOMAP_BUDGET
    history: int = DEFAULT_HISTORY_BUDGET
    tool_results: int = DEFAULT_TOOL_RESULTS_BUDGET


@dataclass
class AssembledContext:
    """The final structured prompt blocks ready for model invocation."""
    system_prompt: str
    messages: List[Dict[str, Any]]
    total_estimated_tokens: int
    pruned_history_count: int = 0
    injected_rules_path: Optional[str] = None


def discover_project_instructions(root_dir: Optional[pathlib.Path | str] = None) -> Tuple[str, Optional[str]]:
    """
    Search for project specification files (ARIA.md, CLAUDE.md, .aria/rules).
    Returns (rules_content, source_path).
    """
    base_path = pathlib.Path(root_dir or os.getcwd()).resolve()
    candidate_files = [
        base_path / "ARIA.md",
        base_path / "CLAUDE.md",
        base_path / ".aria" / "rules.md",
        base_path / ".aria" / "ARIA.md",
    ]

    for candidate in candidate_files:
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    logger.info("Loaded project rules from %s (%d chars)", candidate, len(content))
                    return content, str(candidate)
            except Exception as exc:
                logger.warning("Failed to read project rule file %s: %s", candidate, exc)

    return "", None


class ContextEngine:
    """
    Budget-aware Context Builder and Compactor.
    """

    def __init__(
        self,
        workspace_root: Optional[pathlib.Path | str] = None,
        budgets: Optional[ContextBudgets] = None,
    ) -> None:
        self.workspace_root = pathlib.Path(workspace_root or os.getcwd()).resolve()
        self.budgets = budgets or ContextBudgets()
        self._cached_rules: Optional[Tuple[str, Optional[str]]] = None

    def get_project_rules(self, force_reload: bool = False) -> Tuple[str, Optional[str]]:
        if self._cached_rules is None or force_reload:
            self._cached_rules = discover_project_instructions(self.workspace_root)
        return self._cached_rules

    def estimate_tokens(self, text: str) -> int:
        """Heuristic token count estimation (~3.5 chars per token for English/code, ~1.8 for Chinese)."""
        if not text:
            return 0
        return max(1, len(text) // 3)

    def assemble_system_prompt(self, base_system: str, custom_rules: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """Combine base system instructions with discovered ARIA.md project rules."""
        rules_text, source_path = self.get_project_rules()
        effective_rules = custom_rules if custom_rules is not None else rules_text

        parts = [base_system.strip()]
        if effective_rules:
            # Enforce rules budget
            rules_budget_chars = self.budgets.rules * 3
            if len(effective_rules) > rules_budget_chars:
                effective_rules = effective_rules[:rules_budget_chars] + "\n... [Project rules truncated to fit budget]"
            parts.append(f"\n\n# Project Guidelines & Rules ({pathlib.Path(source_path).name if source_path else 'Custom'})\n\n{effective_rules}")

        return "\n".join(parts), source_path

    def compact_history(
        self,
        messages: Sequence[Dict[str, Any]],
        max_tokens: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Prunes older conversation turns when exceeding the history budget,
        guaranteeing that the initial user prompt and the most recent turns are preserved.
        """
        budget_tokens = max_tokens or self.budgets.history
        if not messages:
            return [], 0

        # Always preserve the very first message (initial objective) if user
        preserved_first = messages[0] if messages[0].get("role") == "user" else None
        remainder = list(messages[1:]) if preserved_first else list(messages)

        current_tokens = sum(self.estimate_tokens(str(m.get("content", ""))) for m in messages)
        if current_tokens <= budget_tokens:
            return list(messages), 0

        # Prune from oldest in remainder until within budget
        pruned_count = 0
        while remainder and current_tokens > budget_tokens:
            dropped = remainder.pop(0)
            dropped_tokens = self.estimate_tokens(str(dropped.get("content", "")))
            current_tokens -= dropped_tokens
            pruned_count += 1

        final_messages = []
        if preserved_first:
            final_messages.append(preserved_first)
            if pruned_count > 0:
                final_messages.append({
                    "role": "system",
                    "content": f"[System: {pruned_count} earlier conversation turns compacted to preserve context budget]"
                })
        final_messages.extend(remainder)
        return final_messages, pruned_count

    def build(
        self,
        base_system_prompt: str,
        messages: Sequence[Dict[str, Any]],
        repo_skeleton: Optional[str] = None,
    ) -> AssembledContext:
        """Assemble full context ready for model invocation."""
        system_prompt, rules_src = self.assemble_system_prompt(base_system_prompt)

        if repo_skeleton:
            repo_chars = self.budgets.repo_map * 3
            if len(repo_skeleton) > repo_chars:
                repo_skeleton = repo_skeleton[:repo_chars] + "\n... [Repo outline truncated]"
            system_prompt += f"\n\n# Codebase Map\n```\n{repo_skeleton}\n```"

        compacted_msgs, pruned = self.compact_history(messages)
        total_tokens = self.estimate_tokens(system_prompt) + sum(
            self.estimate_tokens(str(m.get("content", ""))) for m in compacted_msgs
        )

        return AssembledContext(
            system_prompt=system_prompt,
            messages=compacted_msgs,
            total_estimated_tokens=total_tokens,
            pruned_history_count=pruned,
            injected_rules_path=rules_src,
        )
