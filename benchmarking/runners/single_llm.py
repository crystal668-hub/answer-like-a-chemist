"""Compatibility shim for benchmarking.workflow.runners.single_llm."""

import sys as _sys

from benchmarking.workflow.runners import single_llm as _impl

_sys.modules[__name__] = _impl
