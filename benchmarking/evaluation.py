"""Compatibility shim for benchmarking.scoring.evaluation."""

import sys as _sys

from .scoring import evaluation as _impl

_sys.modules[__name__] = _impl
