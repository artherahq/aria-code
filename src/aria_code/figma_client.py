"""Compatibility import for :mod:`clients.figma_client`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("aria_code.clients.figma_client")
