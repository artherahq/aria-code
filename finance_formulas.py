"""Compatibility import for :mod:`domain.finance_formulas`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("domain.finance_formulas")
