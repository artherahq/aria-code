"""Compatibility import for :mod:`domain.backtest_engine`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("aria_code.domain.backtest_engine")
