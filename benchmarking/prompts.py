"""Compatibility shim for benchmarking.workflow.prompts."""

import sys as _sys

from .workflow import prompts as _impl

_sys.modules[__name__] = _impl
