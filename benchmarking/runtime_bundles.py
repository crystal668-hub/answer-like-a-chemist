"""Compatibility shim for benchmarking.runtime.bundles."""

import sys as _sys

from .runtime import bundles as _impl

_sys.modules[__name__] = _impl
