from __future__ import annotations

import functools
import json
import resource
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


_F = TypeVar("_F", bound=Callable[..., Any])
_active_lock = threading.Lock()
_active_metrics: RuntimeMetrics | None = None


class RuntimeMetrics:
    """Thread-safe, invocation-scoped counters for runtime optimization work."""

    def __init__(self, *, invocation_id: str | None = None) -> None:
        self.invocation_id = invocation_id or str(uuid.uuid4())
        self.output_root: Path | None = None
        self._started = time.monotonic()
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._durations: dict[str, dict[str, float | int]] = {}

    def set_output_root(self, output_root: Path) -> None:
        self.output_root = Path(output_root)

    def increment(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + int(value)

    def duration(self, name: str, seconds: float) -> None:
        value = max(0.0, float(seconds))
        with self._lock:
            bucket = self._durations.setdefault(
                name,
                {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0},
            )
            bucket["count"] = int(bucket["count"]) + 1
            bucket["total_seconds"] = float(bucket["total_seconds"]) + value
            bucket["max_seconds"] = max(float(bucket["max_seconds"]), value)

    def snapshot(self, *, invocation_seconds: float | None = None) -> dict[str, Any]:
        if invocation_seconds is not None:
            self.duration("invocation", invocation_seconds)
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_rss_bytes = int(usage if sys.platform == "darwin" else usage * 1024)
        with self._lock:
            return {
                "schema_version": 1,
                "invocation_id": self.invocation_id,
                "counters": dict(sorted(self._counters.items())),
                "durations": {
                    key: dict(value) for key, value in sorted(self._durations.items())
                },
                "peak_rss_bytes": peak_rss_bytes,
            }

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started


def start_runtime_metrics() -> RuntimeMetrics:
    global _active_metrics
    metrics = RuntimeMetrics()
    with _active_lock:
        _active_metrics = metrics
    return metrics


def finish_runtime_metrics(metrics: RuntimeMetrics) -> dict[str, Any]:
    global _active_metrics
    snapshot = metrics.snapshot(invocation_seconds=metrics.elapsed)
    with _active_lock:
        if _active_metrics is metrics:
            _active_metrics = None
    return snapshot


def active_runtime_metrics() -> RuntimeMetrics | None:
    with _active_lock:
        return _active_metrics


def increment(name: str, value: int = 1) -> None:
    metrics = active_runtime_metrics()
    if metrics is not None:
        metrics.increment(name, value)


@contextmanager
def measure(name: str) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        metrics = active_runtime_metrics()
        if metrics is not None:
            metrics.duration(name, time.monotonic() - started)


def observed_duration(name: str) -> Callable[[_F], _F]:
    def decorate(function: _F) -> _F:
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with measure(name):
                return function(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


def observe_transcript_text(text: str) -> None:
    observe_transcript_snapshot(
        byte_count=len(text.encode("utf-8", errors="replace")),
        line_count=len(text.splitlines()),
    )


def observe_transcript_snapshot(*, byte_count: int, line_count: int) -> None:
    increment("transcript_read_count")
    increment("transcript_bytes", byte_count)
    increment("transcript_lines", line_count)


def observe_transcript_decode(*, succeeded: bool) -> None:
    increment("transcript_json_decode_count")
    if not succeeded:
        increment("transcript_json_decode_error_count")


def decode_transcript_json(line: str) -> Any:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        observe_transcript_decode(succeeded=False)
        raise
    observe_transcript_decode(succeeded=True)
    return value


def classify_write(path: Path) -> str:
    parts = path.parts
    if "per-record" in parts:
        return "per_record"
    if "attempt-results" in parts:
        return "attempt_result"
    if "scoring-pending" in parts:
        return "scoring_pending"
    if path.name == "results.json":
        return "results"
    if path.name == "runtime-manifest.json":
        return "runtime_manifest"
    if path.name == "state.json" and "progress" in parts:
        return "progress_state"
    if "waves" in parts:
        return "wave"
    return "other"


def observe_write(path: Path, byte_count: int) -> None:
    category = classify_write(path)
    increment("state_write_count")
    increment("state_write_bytes", byte_count)
    increment(f"state_write_count.{category}")
    increment(f"state_write_bytes.{category}", byte_count)
