"""Compatibility shim for benchmarking.core.contracts."""

import sys as _sys

from .core import contracts as _impl

_sys.modules[__name__] = _impl
