"""Compatibility shim for benchmarking.analysis.launcher."""

import sys as _sys

from .analysis import launcher as _impl

_sys.modules[__name__] = _impl
