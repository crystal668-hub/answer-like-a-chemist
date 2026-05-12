"""Compatibility shim for benchmarking.skills.health."""

import sys as _sys

from .skills import health as _impl

_sys.modules[__name__] = _impl
