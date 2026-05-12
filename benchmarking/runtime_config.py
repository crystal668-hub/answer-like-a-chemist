"""Compatibility shim for benchmarking.runtime.config_pool."""

import sys as _sys

from .runtime import config_pool as _impl

_sys.modules[__name__] = _impl
