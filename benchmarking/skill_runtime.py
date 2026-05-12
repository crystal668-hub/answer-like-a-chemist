"""Compatibility shim for benchmarking.skills.runtime."""

import sys as _sys

from .skills import runtime as _impl

_sys.modules[__name__] = _impl
