"""Compatibility import for :mod:`tools.realty_data_tools`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("tools.realty_data_tools")
