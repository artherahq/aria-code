"""Compatibility import for :mod:`clients.kling_video_client`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("clients.kling_video_client")
