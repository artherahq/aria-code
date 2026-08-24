"""Compatibility import for :mod:`clients.aliyun_data_client`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("clients.aliyun_data_client")
