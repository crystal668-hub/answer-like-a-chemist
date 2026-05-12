"""Compatibility shim for benchmarking.core.status."""

import sys as _sys

from .core import status as _impl

_sys.modules[__name__] = _impl
