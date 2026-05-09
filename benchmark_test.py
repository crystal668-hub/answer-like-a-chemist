#!/usr/bin/env python3
from __future__ import annotations

import sys
import types
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parent
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

# Compatibility facade: new code should import benchmarking.* directly.
from benchmarking import cli as _benchmark_cli
from benchmarking.cli import *  # noqa: F403
from benchmarking.cli import main


def __getattr__(name: str) -> object:
    return getattr(_benchmark_cli, name)


class _BenchmarkTestFacade(types.ModuleType):
    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if hasattr(_benchmark_cli, name):
            setattr(_benchmark_cli, name, value)


sys.modules[__name__].__class__ = _BenchmarkTestFacade


if __name__ == "__main__":
    raise SystemExit(main())
