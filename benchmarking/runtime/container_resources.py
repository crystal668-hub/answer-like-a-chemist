from __future__ import annotations

import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarking.runtime.observability import increment

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?i?B)?\s*$", re.I)
_SIZE_FACTORS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "eb": 1000**6,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
    "eib": 1024**6,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_size_bytes(value: Any) -> int | None:
    match = _SIZE_RE.match(str(value or ""))
    if not match:
        return None
    factor = _SIZE_FACTORS.get(str(match.group(2) or "").lower())
    if factor is None:
        return None
    return max(0, int(float(match.group(1)) * factor))


def parse_percent(value: Any) -> float | None:
    text = str(value or "").strip().removesuffix("%").strip()
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _pair(value: Any) -> tuple[str, str]:
    parts = [part.strip() for part in str(value or "").split("/", 1)]
    return (parts[0], parts[1]) if len(parts) == 2 else (parts[0], "")


def normalize_docker_stats(payload: dict[str, Any]) -> dict[str, Any]:
    memory_usage, memory_limit = _pair(payload.get("MemUsage"))
    network_rx, network_tx = _pair(payload.get("NetIO"))
    block_read, block_write = _pair(payload.get("BlockIO"))
    try:
        pids = max(0, int(str(payload.get("PIDs") or "0").strip()))
    except ValueError:
        pids = None
    return {
        "cpu_percent": parse_percent(payload.get("CPUPerc")),
        "memory_usage_bytes": parse_size_bytes(memory_usage),
        "memory_limit_bytes": parse_size_bytes(memory_limit),
        "memory_percent": parse_percent(payload.get("MemPerc")),
        "network_rx_bytes": parse_size_bytes(network_rx),
        "network_tx_bytes": parse_size_bytes(network_tx),
        "block_read_bytes": parse_size_bytes(block_read),
        "block_write_bytes": parse_size_bytes(block_write),
        "pids": pids,
    }


class ResourceWindowAccumulator:
    def __init__(self, *, window_seconds: float = 5.0) -> None:
        self.window_seconds = max(0.1, float(window_seconds))
        self._bucket: int | None = None
        self._samples: list[tuple[float, str, dict[str, Any]]] = []

    def add(self, sample: dict[str, Any], *, elapsed_seconds: float, observed_at: str) -> dict[str, Any] | None:
        bucket = int(max(0.0, elapsed_seconds) // self.window_seconds)
        emitted = None
        if self._bucket is not None and bucket != self._bucket:
            emitted = self._flush()
        if self._bucket is None:
            self._bucket = bucket
        self._samples.append((max(0.0, elapsed_seconds), observed_at, sample))
        return emitted

    def finish(self) -> dict[str, Any] | None:
        return self._flush()

    def _flush(self) -> dict[str, Any] | None:
        if not self._samples:
            self._bucket = None
            return None
        samples = self._samples
        self._samples = []
        bucket = int(self._bucket or 0)
        self._bucket = None

        def values(key: str) -> list[float]:
            return [float(sample[key]) for _, _, sample in samples if isinstance(sample.get(key), (int, float))]

        def average(key: str) -> float | None:
            available = values(key)
            return sum(available) / len(available) if available else None

        def maximum(key: str) -> float | int | None:
            available = values(key)
            if not available:
                return None
            result = max(available)
            return int(result) if key != "cpu_percent" and key != "memory_percent" else result

        latest = samples[-1][2]
        return {
            "schema_version": 1,
            "window_index": bucket,
            "window_started_seconds": bucket * self.window_seconds,
            "window_ended_seconds": samples[-1][0],
            "observed_at": samples[-1][1],
            "sample_count": len(samples),
            "cpu_avg_percent": average("cpu_percent"),
            "cpu_peak_percent": maximum("cpu_percent"),
            "memory_avg_bytes": average("memory_usage_bytes"),
            "memory_peak_bytes": maximum("memory_usage_bytes"),
            "memory_limit_bytes": latest.get("memory_limit_bytes"),
            "memory_avg_percent": average("memory_percent"),
            "memory_peak_percent": maximum("memory_percent"),
            "network_rx_bytes": latest.get("network_rx_bytes"),
            "network_tx_bytes": latest.get("network_tx_bytes"),
            "block_read_bytes": latest.get("block_read_bytes"),
            "block_write_bytes": latest.get("block_write_bytes"),
            "pids_last": latest.get("pids"),
            "pids_peak": maximum("pids"),
        }


def summarize_resource_windows(
    windows: list[dict[str, Any]],
    *,
    errors: list[str] | None = None,
    started_at: str = "",
    ended_at: str = "",
) -> dict[str, Any]:
    errors = list(errors or [])
    sample_count = sum(int(row.get("sample_count") or 0) for row in windows)

    def weighted_average(key: str) -> float | None:
        rows = [row for row in windows if isinstance(row.get(key), (int, float))]
        weight = sum(int(row.get("sample_count") or 0) for row in rows)
        if not weight:
            return None
        return sum(float(row[key]) * int(row.get("sample_count") or 0) for row in rows) / weight

    def maximum(key: str, default: float = 0.0) -> float:
        values = [float(row[key]) for row in windows if isinstance(row.get(key), (int, float))]
        return max(values, default=default)

    latest = windows[-1] if windows else {}
    coverage = "unavailable" if not windows else "partial" if errors else "exact"
    return {
        "coverage": coverage,
        "window_seconds": 5.0,
        "window_count": len(windows),
        "sample_count": sample_count,
        "started_at": started_at,
        "ended_at": ended_at,
        "cpu_avg_percent": weighted_average("cpu_avg_percent"),
        "cpu_peak_percent": maximum("cpu_peak_percent"),
        "memory_avg_bytes": weighted_average("memory_avg_bytes"),
        "memory_peak_bytes": int(maximum("memory_peak_bytes")),
        "memory_limit_bytes": latest.get("memory_limit_bytes"),
        "memory_avg_percent": weighted_average("memory_avg_percent"),
        "memory_peak_percent": maximum("memory_peak_percent"),
        "network_rx_bytes": int(latest.get("network_rx_bytes") or 0),
        "network_tx_bytes": int(latest.get("network_tx_bytes") or 0),
        "block_read_bytes": int(latest.get("block_read_bytes") or 0),
        "block_write_bytes": int(latest.get("block_write_bytes") or 0),
        "pids_last": int(latest.get("pids_last") or 0),
        "pids_peak": int(maximum("pids_peak")),
        "errors": errors,
    }


class DockerStatsSampler:
    def __init__(
        self,
        *,
        command: list[str],
        output_path: Path,
        popen: Callable[..., Any] = subprocess.Popen,
        monotonic_clock: Callable[[], float] = time.monotonic,
        window_seconds: float = 5.0,
        heartbeat_path: Path | None = None,
    ) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = monotonic_clock
        self._started_monotonic = self._clock()
        self.started_at = utc_now()
        self.ended_at = ""
        self._accumulator = ResourceWindowAccumulator(window_seconds=window_seconds)
        self._windows: list[dict[str, Any]] = []
        self._errors: list[str] = []
        self._heartbeat_path = heartbeat_path
        self._lock = threading.Lock()
        increment("container_resource_sampler_count")
        self.process = popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=1,
        )
        self._handle = self.output_path.open("a", encoding="utf-8")
        self._thread = threading.Thread(target=self._consume, name="benchmark-docker-stats", daemon=True)
        self._thread.start()

    def _write_window(self, window: dict[str, Any] | None) -> None:
        if window is None:
            return
        with self._lock:
            self._windows.append(window)
            self._handle.write(json.dumps(window, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._handle.flush()
        increment("container_resource_window_count")
        increment("container_resource_sample_count", int(window.get("sample_count") or 0))
        if self._heartbeat_path is not None:
            with suppress(OSError):
                self._heartbeat_path.touch(exist_ok=True)

    def _consume(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            self._errors.append("docker stats stdout unavailable")
            return
        for line in stdout:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("docker stats row is not an object")
                sample = normalize_docker_stats(payload)
                emitted = self._accumulator.add(
                    sample,
                    elapsed_seconds=self._clock() - self._started_monotonic,
                    observed_at=utc_now(),
                )
                self._write_window(emitted)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                self._errors.append(f"{type(exc).__name__}: {exc}")
                increment("container_resource_parse_error_count")

    def stop(self) -> dict[str, Any]:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self._thread.join(timeout=2)
        self._write_window(self._accumulator.finish())
        self.ended_at = utc_now()
        if self.process.returncode not in (None, 0, -15):
            stderr = ""
            if self.process.stderr is not None:
                stderr = self.process.stderr.read().strip()
            self._errors.append(stderr[:1000] or f"docker stats exited {self.process.returncode}")
        self._handle.flush()
        with suppress(OSError):
            os.fsync(self._handle.fileno())
        self._handle.close()
        return summarize_resource_windows(
            list(self._windows),
            errors=self._errors,
            started_at=self.started_at,
            ended_at=self.ended_at,
        )
