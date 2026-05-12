"""Compatibility shim for benchmarking.runtime.config."""

import sys as _sys

from .runtime import config as _impl

_sys.modules[__name__] = _impl
