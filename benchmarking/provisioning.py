"""Compatibility shim for benchmarking.runtime.provisioning."""

import sys as _sys

from .runtime import provisioning as _impl

_sys.modules[__name__] = _impl
