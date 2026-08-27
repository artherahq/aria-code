"""quant_engine — vendored commodity quant modules (sports + stochastic).

Elo/Dixon-Coles football prediction and Black-Scholes/Monte-Carlo/Kelly
options+derivatives math. Proprietary factor/ML/strategy engines stay in the
private Arthera platform.

Open-core boundary: callers MUST go through ``is_available()`` and degrade
gracefully when it returns False. Today the source is bundled, so it is always
available — but routing every use through this boundary means the day the engine
is split into a separate compiled (.so) wheel, the free shell keeps working with
no caller changes (see tools/build_quant_engine.py, CLOSING_SOURCE.md).
"""

from importlib.util import find_spec
from pathlib import Path

# ``packages`` is a namespace facade into the private Arthera platform, but
# this public shell also bundles a small ``quant_engine`` package.  Python stops
# namespace lookup once it finds this regular subpackage, so extend *this*
# package path as well; otherwise ``packages.quant_engine.services`` (the
# audited A-share prediction engine) is silently unreachable from the CLI.
_private_quant_engine = Path(__file__).resolve().parents[5] / "Arthera" / "packages" / "quant_engine"
if _private_quant_engine.is_dir() and str(_private_quant_engine) not in __path__:
    __path__.append(str(_private_quant_engine))

__version__ = "1.0.0"


def is_available() -> bool:
    """True if the quant engine (bundled source or compiled wheel) can be imported."""
    try:
        return find_spec("packages.quant_engine.stochastic") is not None
    except Exception:
        return False
