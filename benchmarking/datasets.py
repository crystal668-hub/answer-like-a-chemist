"""Compatibility shim for benchmarking.core.datasets."""

import sys as _sys

from .core import datasets as _impl

_sys.modules[__name__] = _impl
