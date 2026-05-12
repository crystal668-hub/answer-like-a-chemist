"""Compatibility shim for benchmarking.workflow.orchestration."""

import sys as _sys

from .workflow import orchestration as _impl

_sys.modules[__name__] = _impl
