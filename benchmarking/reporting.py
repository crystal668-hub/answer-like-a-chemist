"""Compatibility shim for benchmarking.core.reporting."""

import sys as _sys

from .core import reporting as _impl

_sys.modules[__name__] = _impl
