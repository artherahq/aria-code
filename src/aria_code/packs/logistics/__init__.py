"""Logistics — waybills, containers, carriers.

What makes this pack safe to have switched on is that every identifier it
recognises is *verifiable*, not merely shaped right.  A bare run of twelve
digits appears in invoices, timestamps, phone numbers and order IDs; a
container number carries a check digit that can be recomputed, and a UPS or SF
tracking number carries a prefix that nothing else uses.

So the resolver reports both kinds and scores them differently: verified
identifiers activate the pack, shape-only matches are reported below the
activation threshold so the CLI can offer "did you mean this waybill?" without
answering a code question with a shipment lookup on its own initiative.  That
asymmetry is the contract's rule applied to a domain whose identifiers are
almost all digits.
"""

from __future__ import annotations

import re
from typing import Sequence

from aria_code.packs.base import BaseDomainPack, EntityMatch, PackActivation

PACK_NAME = "logistics"

LOGISTICS_TOOLS = (
    "analyze_logistics_data",
)

_VERIFIED = 0.95
_SHAPE_ONLY = 0.3

# Carrier prefixes that no other identifier uses.
_CARRIER_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    # UPS: 1Z + 6-char shipper + 2-char service + 8-digit sequence.
    ("ups", re.compile(r"\b(1Z[0-9A-Z]{16})\b", re.I)),
    # SF Express domestic waybill.
    ("sf", re.compile(r"\b(SF\d{12,15})\b", re.I)),
    # DHL express, 'JD' + 20 digits (also used on some waybill labels).
    ("dhl", re.compile(r"\b(JD\d{18,20})\b")),
)

# ISO 6346 container: 4 letters (owner + category) + 6 digits + check digit.
_CONTAINER = re.compile(r"\b([A-Z]{4}\d{7})\b")

# Bare numeric runs — the shape most domestic waybills take, and also the shape
# of an order id, an invoice number and a unix timestamp in milliseconds.
_BARE_DIGITS = re.compile(r"(?<!\d)(\d{10,15})(?!\d)")

# ISO 6346 letter values: A=10, then ascending, skipping every multiple of 11.
def _iso6346_letter_values() -> dict[str, int]:
    values: dict[str, int] = {}
    value = 10
    for index in range(26):
        while value % 11 == 0:
            value += 1
        values[chr(ord("A") + index)] = value
        value += 1
    return values


_LETTER_VALUES = _iso6346_letter_values()


def is_valid_container(code: str) -> bool:
    """True when *code* satisfies the ISO 6346 check digit.

    This is what separates a container number from four capitals followed by
    seven digits, which is also the shape of plenty of part numbers and
    internal SKUs. Recomputing the check digit turns a guess into a
    resolution — the distinction the pack contract is built on.
    """
    text = (code or "").strip().upper()
    if len(text) != 11 or not text[:4].isalpha() or not text[4:].isdigit():
        return False
    total = 0
    for position, char in enumerate(text[:10]):
        value = _LETTER_VALUES[char] if char.isalpha() else int(char)
        total += value * (2 ** position)
    return (total % 11) % 10 == int(text[10])


class LogisticsPack(BaseDomainPack):
    """Recognises shipment identifiers and owns the freight workflows."""

    name = PACK_NAME

    def resolve_entities(self, message: str) -> Sequence[EntityMatch]:
        text = message or ""
        if not text.strip():
            return ()

        seen: set[str] = set()
        claimed: list[tuple[int, int]] = []
        entities: list[EntityMatch] = []

        def _add(value: str, kind: str, surface: str, span: tuple[int, int], confidence: float) -> None:
            if value in seen:
                return
            seen.add(value)
            claimed.append(span)
            entities.append(EntityMatch(
                pack=PACK_NAME, kind=kind, value=value,
                surface=surface, position=span[0], confidence=confidence,
            ))

        for carrier, pattern in _CARRIER_PATTERNS:
            for match in pattern.finditer(text):
                _add(match.group(1).upper(), f"waybill:{carrier}",
                     match.group(1), match.span(1), _VERIFIED)

        for match in _CONTAINER.finditer(text):
            code = match.group(1)
            _add(code, "container", code, match.span(1),
                 _VERIFIED if is_valid_container(code) else _SHAPE_ONLY)

        # Bare digit runs last, and only outside what a verified identifier
        # already claimed: the digits inside `1Z999AA10123456784` are not a
        # second, weaker waybill, and reporting them as one would let the UI
        # offer a "did you mean" for a number the user never wrote.
        for match in _BARE_DIGITS.finditer(text):
            start, end = match.span(1)
            if any(start >= c_start and end <= c_end for c_start, c_end in claimed):
                continue
            _add(match.group(1), "waybill", match.group(1), match.span(1), _SHAPE_ONLY)

        return tuple(entities)

    def tool_names(self) -> Sequence[str]:
        return LOGISTICS_TOOLS

    def prompt_fragment(self, activation: PackActivation) -> str:
        kinds = sorted({e.kind for e in activation.entities})
        ids = ", ".join(sorted({e.value for e in activation.entities}))
        return (
            f"物流标识已识别（{', '.join(kinds)}）：{ids}。\n"
            "用工具取回的运单/箱单数据回答，不要凭记忆编造承运商、时效或费率；"
            "标注数据来源与时间戳。金额与账期以对账结果为准。"
        )

    def acceptance_commands(self, activation: PackActivation) -> Sequence[str]:
        # Looked up through the module rather than a name bound at import, so
        # what this workspace declares is read when the turn runs — a .ariarc
        # edited mid-session takes effect on the next message, not the next
        # process.
        from aria_code.packs import rules

        return rules.acceptance_commands_for(PACK_NAME)


LOGISTICS_PACK = LogisticsPack()


def register() -> LogisticsPack:
    """Register the logistics pack.  Idempotent."""
    from aria_code.packs.registry import register_pack

    register_pack(LOGISTICS_PACK)
    return LOGISTICS_PACK


__all__ = [
    "LOGISTICS_PACK",
    "LOGISTICS_TOOLS",
    "PACK_NAME",
    "LogisticsPack",
    "is_valid_container",
    "register",
]
