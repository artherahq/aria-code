"""The domain pack contract.

A domain pack bundles everything Aria needs to serve one subject area —
finance, logistics, clinical, accounting — behind a single interface, so the
core never learns any of them.

Why this exists
---------------
Finance was not a layer in this codebase; it was the substrate.  ``finance``
and ``analysis`` were core intent labels, ~8,000 lines of ticker tables and
market handlers sat under ``apps/cli``, and the deterministic chain ran the
strategy, realty, and stock-chart handlers against *every* message before the
model saw it.  The costs were concrete: a question about this repository was
answered with a MongoDB stock quote (the string "MongoDB" in a previous reply
resolved to the ticker MDB), and a message containing the word "行情" blocked
the REPL for tens of seconds downloading the full A-share and HK symbol
universe.  A logistics or clinical user would have paid exactly the same tax.

The rule that prevents all of it
--------------------------------
**A pack activates only when it resolves a concrete entity from the user's own
message.**  Domain vocabulary alone never activates a pack.  "分析" is ordinary
Chinese; "AAPL" is a ticker.  Only the second may switch on the finance pack,
and nothing a *previous assistant reply* said can switch on anything.

That single rule is what makes packs composable: an inactive pack contributes
no handlers, no tools, and no prompt text, so N domains cost O(N) to maintain
and O(1) at request time rather than N×M branches in one router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

# A pack must clear this to activate.  Resolvers report how sure they are that
# the surface text really names an entity of their kind; a bare uppercase word
# that merely *looks* like a ticker should score below it.
DEFAULT_ACTIVATION_THRESHOLD = 0.5


@dataclass(frozen=True)
class EntityMatch:
    """One concrete thing a pack recognised in a message.

    ``value`` is the canonical identifier the pack's tools accept (a ticker, a
    waybill number, an ICD code).  ``surface`` is the text as the user actually
    wrote it, kept so the UI can show what was matched and the user can correct
    a wrong resolution.
    """

    pack: str
    kind: str
    value: str
    surface: str = ""
    position: int = 0
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.pack:
            raise ValueError("EntityMatch.pack is required")
        if not self.kind:
            raise ValueError("EntityMatch.kind is required")
        if not self.value:
            raise ValueError("EntityMatch.value is required")


@dataclass(frozen=True)
class PackActivation:
    """A pack that claimed the message, with what it claimed."""

    pack: str
    entities: tuple[EntityMatch, ...] = field(default_factory=tuple)

    @property
    def confidence(self) -> float:
        return max((e.confidence for e in self.entities), default=0.0)

    @property
    def primary(self) -> EntityMatch | None:
        """Highest-confidence entity, earliest in the message on a tie."""
        if not self.entities:
            return None
        return sorted(self.entities, key=lambda e: (-e.confidence, e.position))[0]


@runtime_checkable
class DomainPack(Protocol):
    """What a subject area must provide to plug into Aria.

    Every method is optional beyond ``name`` and ``resolve_entities`` — a pack
    that only teaches Aria to recognise its identifiers is already useful,
    because recognition is what gates everything else.
    """

    name: str

    def resolve_entities(self, message: str) -> Sequence[EntityMatch]:
        """Return the entities this pack recognises in *message*.

        Must be cheap and must not perform blocking network I/O: this runs on
        every message, for every registered pack.  Resolve from local tables or
        a warm cache and return nothing when unsure — a missed activation is
        recoverable, a wrong one silently answers the wrong question.
        """
        ...

    def handlers(self) -> Sequence[object]:
        """Deterministic handlers, run only while this pack is active."""
        ...

    def tool_names(self) -> Sequence[str]:
        """Tools to expose to the model only while this pack is active."""
        ...

    def prompt_fragment(self, activation: PackActivation) -> str:
        """Domain guidance to add to the system prompt while active."""
        ...

    def acceptance_commands(self, activation: PackActivation) -> Sequence[str]:
        """Commands that decide whether work in this domain is *correct*.

        This is the pack's connection to the acceptance gate, and it is the
        reason the pack contract scales past software.  Every domain defines
        "right" differently, but they all express it the same way: a command
        that exits non-zero when the work is wrong.  Software says
        ``pytest -q``; a logistics pack says "run the reconciliation script,
        it exits non-zero when the ledger does not balance"; a data pack says
        "validate against the schema".

        So a pack does not have to teach the core its subject.  It only has to
        say what green looks like, and the closed loop it gets in return is
        the same one the software case gets.

        Returning nothing is the normal case: inference from the workspace
        then decides, exactly as before.
        """
        ...


class BaseDomainPack:
    """Convenience base implementing the optional half of the protocol."""

    name: str = ""

    def resolve_entities(self, message: str) -> Sequence[EntityMatch]:
        raise NotImplementedError

    def handlers(self) -> Sequence[object]:
        return ()

    def tool_names(self) -> Sequence[str]:
        return ()

    def prompt_fragment(self, activation: PackActivation) -> str:
        return ""

    def acceptance_commands(self, activation: PackActivation) -> Sequence[str]:
        return ()


__all__ = [
    "DEFAULT_ACTIVATION_THRESHOLD",
    "BaseDomainPack",
    "DomainPack",
    "EntityMatch",
    "PackActivation",
]
