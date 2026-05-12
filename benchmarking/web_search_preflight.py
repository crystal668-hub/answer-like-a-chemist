"""Compatibility shim for benchmarking.runtime.web_search_preflight."""

import sys as _sys

from .runtime import web_search_preflight as _impl

_sys.modules[__name__] = _impl
