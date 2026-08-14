"""Compatibility import for :mod:`domain.strategy_vault`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("domain.strategy_vault")
