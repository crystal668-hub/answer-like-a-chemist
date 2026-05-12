"""Compatibility shim for benchmarking.workflow.runners.chemqa."""

import sys as _sys

from benchmarking.workflow.runners import chemqa as _impl

_sys.modules[__name__] = _impl
