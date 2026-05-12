"""Compatibility shim for benchmarking.core.convergence."""

import sys as _sys

from .core import convergence as _impl

_sys.modules[__name__] = _impl
