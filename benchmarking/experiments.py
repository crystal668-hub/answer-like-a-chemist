"""Compatibility shim for benchmarking.core.experiments."""

import sys as _sys

from .core import experiments as _impl

_sys.modules[__name__] = _impl
