"""Compatibility import for :mod:`clients.openai_image_client`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("aria_code.clients.openai_image_client")
