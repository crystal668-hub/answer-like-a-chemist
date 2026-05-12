"""Compatibility shim for benchmarking.core.result_contract."""

import sys as _sys

from .core import result_contract as _impl

_sys.modules[__name__] = _impl
