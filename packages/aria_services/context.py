"""Context management service for Aria Code.

This module owns deterministic context pressure and local compaction behavior.
LLM-based summarisation remains an adapter concern, but the prompt/envelope
shape is defined here so CLI, daemon, and future channels can share it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List


def _int_or(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float_or(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass(frozen=True)
class ContextPolicy:
    max_tokens: int = 16384
    threshold: float = 0.78
    min_messages: int = 8
    target_ratio: float = 0.55
    compact_ratio: float = 0.70
    tail_messages: int = 8
    summary_tail_messages: int = 6

    def normalized(self) -> "ContextPolicy":
        max_tokens = max(1024, _int_or(self.max_tokens, 16384))
        threshold = max(0.50, min(0.95, _float_or(self.threshold, 0.78)))
        min_messages = max(1, _int_or(self.min_messages, 8))
        target_ratio = max(0.20, min(0.85, _float_or(self.target_ratio, 0.55)))
        compact_ratio = max(target_ratio, min(0.90, _float_or(self.compact_ratio, 0.70)))
        tail_messages = max(2, _int_or(self.tail_messages, 8))
        summary_tail_messages = max(2, _int_or(self.summary_tail_messages, 6))
        return ContextPolicy(
            max_tokens=max_tokens,
            threshold=threshold,
            min_messages=min_messages,
            target_ratio=target_ratio,
            compact_ratio=compact_ratio,
            tail_messages=tail_messages,
            summary_tail_messages=summary_tail_messages,
        )


@dataclass(frozen=True)
class ContextDecision:
    should_compact: bool
    estimated_tokens: int
    max_tokens: int
    fill_ratio: float
    fill_pct: int
    threshold: float
    message_count: int
    reason: str = ""
    target_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextSummaryEnvelope:
    messages: List[Dict[str, str]]
    old_message_count: int
    new_message_count: int
    tail_message_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContextService:
    """Pure context service with no terminal, Rich, or LLM dependency."""

    def __init__(self, policy: ContextPolicy | None = None):
        self.policy = (policy or ContextPolicy()).normalized()

    @staticmethod
    def estimate_message_tokens(messages: Iterable[dict], extra_content: str = "") -> int:
        total_chars = sum(len(str(message.get("content", ""))) for message in messages)
        total_chars += len(str(extra_content or ""))
        return total_chars // 3

    def compaction_decision(self, messages: List[dict], extra_content: str = "") -> ContextDecision:
        estimated = self.estimate_message_tokens(messages, extra_content=extra_content)
        max_tokens = max(self.policy.max_tokens, 1)
        fill_ratio = estimated / max_tokens
        message_count = len(messages)
        reason = ""
        should_compact = False
        if message_count < self.policy.min_messages:
            reason = "message_count_below_minimum"
        elif fill_ratio >= self.policy.threshold:
            reason = "threshold_exceeded"
            should_compact = True
        else:
            reason = "below_threshold"
        return ContextDecision(
            should_compact=should_compact,
            estimated_tokens=estimated,
            max_tokens=max_tokens,
            fill_ratio=fill_ratio,
            fill_pct=min(100, int(fill_ratio * 100)),
            threshold=self.policy.threshold,
            message_count=message_count,
            reason=reason,
            target_tokens=int(max_tokens * self.policy.target_ratio),
        )

    def compact_messages(self, messages: List[dict], max_chars: int = 0) -> List[dict]:
        """Compact history locally while preserving recent turns and errors."""

        if max_chars <= 0:
            max_chars = int(self.policy.max_tokens * 3 * self.policy.compact_ratio)

        total = sum(len(str(message.get("content", ""))) for message in messages)
        if total <= max_chars or len(messages) <= self.policy.tail_messages:
            return messages

        system = messages[0]
        keep_tail = min(self.policy.tail_messages, max(2, len(messages) - 1))
        middle = messages[1:-keep_tail]
        tail = messages[-keep_tail:]

        compacted: List[dict] = [system]
        for message in middle:
            compacted.append(self._compact_middle_message(message))
        compacted.extend(tail)
        return compacted

    def _compact_middle_message(self, message: dict) -> dict:
        content = str(message.get("content", ""))
        role = str(message.get("role", ""))

        if role == "tool" and len(content) > 200:
            lines = content.splitlines()
            kept: List[str] = []
            has_error = False
            for line in lines[:30]:
                stripped = line.strip()
                if not stripped:
                    continue
                low = stripped.lower()
                if any(keyword in low for keyword in ("error", "traceback", "exception", "failed", "failure")):
                    kept.append(stripped)
                    has_error = True
                elif len(kept) < 4 and len(stripped) > 8:
                    kept.append(stripped)
            summary = " | ".join(kept[:4]) if kept else content[:150]
            flag = " [error preserved]" if has_error else " [compacted]"
            return {"role": role, "content": f"{summary}{flag}"}

        if role == "assistant" and len(content) > 500:
            paras = [part.strip() for part in content.split("\n\n") if part.strip()]
            if len(paras) >= 2:
                head = paras[0][:280]
                tail = paras[-1][-180:]
                return {"role": role, "content": f"{head}\n...\n{tail} [compacted]"}
            return {"role": role, "content": content[:350] + "... [compacted]"}

        return message

    def build_summary_transcript(self, messages: List[dict]) -> str:
        parts: List[str] = []
        for message in messages:
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            if role == "tool":
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                has_error = any(
                    "error" in line.lower() or "traceback" in line.lower()
                    for line in lines[:10]
                )
                excerpt = " | ".join(lines[:3]) if lines else content[:200]
                label = "Tool[error]" if has_error else "Tool"
                parts.append(f"{label}: {excerpt[:300]}")
            elif role == "user":
                parts.append(f"User: {content[:800]}")
            else:
                parts.append(f"Aria: {content[:1200]}")
        return "\n\n".join(parts)

    def build_summary_prompt(self, messages: List[dict]) -> str:
        transcript = self.build_summary_transcript(messages)
        return (
            "You are a context compressor for a quantitative finance AI assistant.\n"
            "Given the conversation transcript, produce a DENSE SUMMARY (<=350 words).\n"
            "You MUST preserve:\n"
            "  - All ticker symbols / asset names discussed\n"
            "  - Key numerical results (prices, rates, backtest metrics)\n"
            "  - Code files written or modified (file paths + purpose)\n"
            "  - Errors encountered and how they were resolved\n"
            "  - User preferences or decisions made\n"
            "  - The last task status (complete / in-progress / blocked)\n"
            "Write in concise third-person present tense. "
            "Start with: 'Session summary: ...'\n\n"
            f"TRANSCRIPT:\n{transcript}\n\nSUMMARY:"
        )

    def build_summary_envelope(self, messages: List[dict], summary: str) -> ContextSummaryEnvelope:
        tail_count = min(self.policy.summary_tail_messages, len(messages))
        tail = messages[-tail_count:] if tail_count else []
        envelope_messages = [
            {
                "role": "user",
                "content": (
                    "[Session summary - earlier conversation compressed]\n\n"
                    f"{summary.strip()}\n\n"
                    "[Recent conversation follows]"
                ),
            },
            {
                "role": "assistant",
                "content": "Summary loaded. Continuing with the current task.",
            },
            *tail,
        ]
        return ContextSummaryEnvelope(
            messages=envelope_messages,
            old_message_count=len(messages),
            new_message_count=len(envelope_messages),
            tail_message_count=tail_count,
        )


def build_context_service(
    *,
    max_tokens: int = 16384,
    threshold: float = 0.78,
    min_messages: int = 8,
    target_ratio: float = 0.55,
    compact_ratio: float = 0.70,
    tail_messages: int = 8,
    summary_tail_messages: int = 6,
) -> ContextService:
    return ContextService(
        ContextPolicy(
            max_tokens=max_tokens,
            threshold=threshold,
            min_messages=min_messages,
            target_ratio=target_ratio,
            compact_ratio=compact_ratio,
            tail_messages=tail_messages,
            summary_tail_messages=summary_tail_messages,
        )
    )
