"""Compatibility entrypoint for benchmarking.runtime.single_llm_openclaw_wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarking.runtime import single_llm_openclaw_wrapper as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
