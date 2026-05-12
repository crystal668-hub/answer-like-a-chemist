"""Compatibility shim for benchmarking.scoring.evaluators."""

import sys as _sys

from .scoring import evaluators as _impl

_sys.modules[__name__] = _impl
