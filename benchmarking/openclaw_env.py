"""Compatibility shim for benchmarking.runtime.openclaw_env."""

import sys as _sys

from .runtime import openclaw_env as _impl

_sys.modules[__name__] = _impl
