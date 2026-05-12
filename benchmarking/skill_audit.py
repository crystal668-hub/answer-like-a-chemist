"""Compatibility shim for benchmarking.skills.audit."""

import sys as _sys

from .skills import audit as _impl

_sys.modules[__name__] = _impl
