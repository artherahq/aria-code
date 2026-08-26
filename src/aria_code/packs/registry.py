"""Domain pack registration and entity-gated activation.

Activation is the whole point of this module.  A pack is switched on for a
message only when it resolves a concrete entity *from that message*, at or
above a confidence threshold.  Three consequences follow, each of which fixes a
failure this codebase actually shipped:

  - **Domain vocabulary cannot activate a pack.**  "根据以上分析和建议开始完善"
    contains 分析 but names nothing; no pack claims it, so no market handler,
    no quote, no ticker.
  - **Conversation history cannot activate a pack.**  Only the user's current
    message is offered to resolvers.  An assistant reply that mentioned
    "Cassandra/MongoDB" can no longer donate the ticker MDB to the next turn.
  - **An inactive pack costs nothing.**  It contributes no handlers, no tools
    and no prompt text, so adding a clinical or logistics pack does not slow
    down or perturb a finance user, and vice versa.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from aria_code.packs.base import (
    DEFAULT_ACTIVATION_THRESHOLD,
    DomainPack,
    EntityMatch,
    PackActivation,
)

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, DomainPack] = {}


def register_pack(pack: DomainPack) -> None:
    """Register *pack* under its name, replacing any earlier registration."""
    name = getattr(pack, "name", "")
    if not name:
        raise ValueError("a domain pack must declare a non-empty name")
    if not hasattr(pack, "resolve_entities"):
        raise TypeError(f"pack {name!r} does not implement resolve_entities()")
    _REGISTRY[name] = pack


def unregister_pack(name: str) -> None:
    _REGISTRY.pop(name, None)


def registered_packs() -> tuple[DomainPack, ...]:
    return tuple(_REGISTRY.values())


def get_pack(name: str) -> DomainPack | None:
    return _REGISTRY.get(name)


def clear_registry() -> None:
    """Drop every registration (tests)."""
    _REGISTRY.clear()


def _entities_from(pack: DomainPack, message: str) -> list[EntityMatch]:
    """Ask one pack what it recognises, never letting it break the request.

    A resolver runs on every message; a pack that raises, or that returns
    something malformed, must degrade to "recognises nothing" rather than take
    the CLI down with it.
    """
    try:
        found = pack.resolve_entities(message) or ()
    except Exception:
        logger.debug("pack %r failed to resolve entities", getattr(pack, "name", "?"),
                     exc_info=True)
        return []
    out: list[EntityMatch] = []
    for entity in found:
        if isinstance(entity, EntityMatch):
            out.append(entity)
    return out


def activate_packs(
    message: str,
    *,
    packs: Iterable[DomainPack] | None = None,
    threshold: float = DEFAULT_ACTIVATION_THRESHOLD,
) -> tuple[PackActivation, ...]:
    """Return the packs that claim *message*, most confident first.

    Only the current user message is considered.  Callers must not pass
    conversation history here: inheriting an entity across turns is what
    produced a stock quote in reply to "根据以上分析和建议开始完善".
    """
    if not (message or "").strip():
        return ()

    candidates = tuple(packs) if packs is not None else registered_packs()
    activations: list[PackActivation] = []
    for pack in candidates:
        entities = [
            e for e in _entities_from(pack, message) if e.confidence >= threshold
        ]
        if entities:
            activations.append(
                PackActivation(
                    pack=getattr(pack, "name", ""),
                    entities=tuple(entities),
                )
            )
    activations.sort(key=lambda a: -a.confidence)
    return tuple(activations)


def active_handlers(activations: Sequence[PackActivation]) -> tuple[object, ...]:
    """Deterministic handlers contributed by the active packs, in order."""
    handlers: list[object] = []
    for activation in activations:
        pack = get_pack(activation.pack)
        if pack is None:
            continue
        try:
            handlers.extend(pack.handlers() or ())
        except Exception:
            logger.debug("pack %r failed to provide handlers", activation.pack,
                         exc_info=True)
    return tuple(handlers)


def active_tool_names(activations: Sequence[PackActivation]) -> tuple[str, ...]:
    """Tool names to expose to the model for this message."""
    names: list[str] = []
    for activation in activations:
        pack = get_pack(activation.pack)
        if pack is None:
            continue
        try:
            for name in pack.tool_names() or ():
                if name and name not in names:
                    names.append(str(name))
        except Exception:
            logger.debug("pack %r failed to provide tool names", activation.pack,
                         exc_info=True)
    return tuple(names)


def active_prompt_fragments(activations: Sequence[PackActivation]) -> tuple[str, ...]:
    """Domain guidance to append to the system prompt for this message."""
    fragments: list[str] = []
    for activation in activations:
        pack = get_pack(activation.pack)
        if pack is None:
            continue
        try:
            fragment = pack.prompt_fragment(activation) or ""
        except Exception:
            logger.debug("pack %r failed to provide a prompt fragment", activation.pack,
                         exc_info=True)
            continue
        if fragment.strip():
            fragments.append(fragment.strip())
    return tuple(fragments)


def active_acceptance_commands(activations: Sequence[PackActivation]) -> tuple[str, ...]:
    """Verification commands contributed by the active packs, deduplicated.

    Order follows activation confidence, so the pack that most clearly owns
    the message gets its check run first — and the acceptance gate stops at
    the first red, which makes that ordering the difference between a useful
    failure and a confusing one.
    """
    commands: list[str] = []
    for activation in activations:
        pack = get_pack(activation.pack)
        if pack is None:
            continue
        try:
            found = pack.acceptance_commands(activation) or ()
        except Exception:
            logger.debug("pack %r failed to provide acceptance commands", activation.pack,
                         exc_info=True)
            continue
        for command in found:
            text = str(command or "").strip()
            if text and text not in commands:
                commands.append(text)
    return tuple(commands)


__all__ = [
    "activate_packs",
    "active_acceptance_commands",
    "active_handlers",
    "active_prompt_fragments",
    "active_tool_names",
    "clear_registry",
    "get_pack",
    "register_pack",
    "registered_packs",
    "unregister_pack",
]
