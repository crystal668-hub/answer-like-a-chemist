"""Compatibility shim for benchmarking.skills.tree."""

import sys as _sys

from .skills import tree as _impl

_sys.modules[__name__] = _impl
