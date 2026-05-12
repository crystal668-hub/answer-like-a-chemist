"""Compatibility shim for benchmarking.runtime.session_isolation."""

import sys as _sys

from .runtime import session_isolation as _impl

_sys.modules[__name__] = _impl
