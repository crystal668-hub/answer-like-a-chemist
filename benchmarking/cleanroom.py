"""Compatibility shim for benchmarking.runtime.cleanroom."""

import sys as _sys

from .runtime import cleanroom as _impl

_sys.modules[__name__] = _impl
