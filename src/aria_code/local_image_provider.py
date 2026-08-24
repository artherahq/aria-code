"""Compatibility import for :mod:`providers.local_image_provider`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("providers.local_image_provider")
