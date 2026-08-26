"""Where a pack learns what "correct" means in *this* workspace.

A pack knows the shape of its domain — that a waybill is a waybill, that
``ch_3P…`` is a Stripe charge.  It cannot know how a particular company checks
that work on those things was done right: which script reconciles their ledger,
which job validates their schema, what their build is called.  That knowledge
belongs to the workspace, not to the pack, and it is the one thing that has to
come from outside for the closed loop to mean anything in a domain the core
never learned.

So packs read it from the project's own ``.ariarc``::

    "acceptance": {
      "default":   ["python3 -m pytest -q"],
      "logistics": ["python3 scripts/reconcile_waybills.py"],
      "payments":  ["python3 scripts/verify_stripe_sync.py"]
    }

``default`` applies to every turn.  A pack-named list applies only when that
pack resolved a concrete entity from the user's message — the same activation
rule that governs everything else a pack contributes, so declaring a logistics
check cannot slow down or perturb a payments question.

This is the whole productisation story for a new industry: a company does not
extend Aria to adopt it, they declare what green looks like and the loop they
already have starts holding that line.
"""

from __future__ import annotations

import logging
from typing import Sequence

logger = logging.getLogger(__name__)

DEFAULT_KEY = "default"


def _coerce(value: object) -> tuple[str, ...]:
    """Accept a string or a list of them; ignore anything else.

    Hand-edited config is the input here, so a single command written as a
    bare string is the expected mistake, not an error worth failing a turn
    over.
    """
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
        return tuple(out)
    return ()


def acceptance_commands_for(pack_name: str, *, rc: object = None) -> tuple[str, ...]:
    """Commands this workspace declares for *pack_name*.

    Never raises: a malformed or missing ``.ariarc`` means "nothing declared",
    which falls back to inference from the workspace — the behaviour before
    any of this existed.
    """
    config = rc
    if config is None:
        try:
            from aria_code.ariarc import get_ariarc

            config = get_ariarc()
        except Exception:
            logger.debug("could not load .ariarc for acceptance commands", exc_info=True)
            return ()

    table = getattr(config, "acceptance", None)
    if not isinstance(table, dict):
        return ()
    return _coerce(table.get(pack_name))


def default_acceptance_commands(*, rc: object = None) -> tuple[str, ...]:
    """Commands this workspace declares for every turn, pack or not."""
    return acceptance_commands_for(DEFAULT_KEY, rc=rc)


__all__ = [
    "DEFAULT_KEY",
    "acceptance_commands_for",
    "default_acceptance_commands",
]
