from __future__ import annotations

import io
from pathlib import Path

from benchmarking.runtime.container_resources import (
    DockerStatsSampler,
    ResourceWindowAccumulator,
    normalize_docker_stats,
    parse_size_bytes,
    summarize_resource_windows,
)


def test_parse_docker_units_and_normalize_stats() -> None:
    assert parse_size_bytes("1.5MiB") == 1572864
    assert parse_size_bytes("2 GB") == 2_000_000_000
    sample = normalize_docker_stats(
        {
            "CPUPerc": "25.5%",
            "MemUsage": "128MiB / 1GiB",
            "MemPerc": "12.5%",
            "NetIO": "2kB / 3kB",
            "BlockIO": "4MB / 5MB",
            "PIDs": "7",
        }
    )
    assert sample["cpu_percent"] == 25.5
    assert sample["memory_usage_bytes"] == 128 * 1024 * 1024
    assert sample["network_tx_bytes"] == 3000
    assert sample["pids"] == 7


def test_resource_windows_preserve_average_peak_and_short_tail() -> None:
    accumulator = ResourceWindowAccumulator(window_seconds=5)
    sample1 = {"cpu_percent": 10.0, "memory_usage_bytes": 100, "memory_limit_bytes": 1000, "memory_percent": 10.0, "network_rx_bytes": 1, "network_tx_bytes": 2, "block_read_bytes": 3, "block_write_bytes": 4, "pids": 1}
    sample2 = {**sample1, "cpu_percent": 30.0, "memory_usage_bytes": 300, "network_rx_bytes": 5, "pids": 4}

    assert accumulator.add(sample1, elapsed_seconds=0.5, observed_at="t1") is None
    assert accumulator.add(sample2, elapsed_seconds=4.9, observed_at="t2") is None
    first = accumulator.add(sample1, elapsed_seconds=5.1, observed_at="t3")
    tail = accumulator.finish()

    assert first["sample_count"] == 2
    assert first["cpu_avg_percent"] == 20.0
    assert first["memory_peak_bytes"] == 300
    assert tail["sample_count"] == 1
    summary = summarize_resource_windows([first, tail])
    assert summary["coverage"] == "exact"
    assert summary["cpu_peak_percent"] == 30.0
    assert summary["pids_peak"] == 4


def test_resource_summary_degrades_on_sampler_error() -> None:
    summary = summarize_resource_windows([], errors=["daemon unavailable"])
    assert summary["coverage"] == "unavailable"
    assert summary["errors"] == ["daemon unavailable"]


def test_sampler_stops_owned_stats_process_and_flushes_tail(tmp_path: Path) -> None:
    class Process:
        def __init__(self) -> None:
            self.stdout = io.StringIO(
                '{"CPUPerc":"10%","MemUsage":"1MiB / 2MiB","MemPerc":"50%",'
                '"NetIO":"1kB / 2kB","BlockIO":"3kB / 4kB","PIDs":"2"}\n'
            )
            self.stderr = io.StringIO("")
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = Process()
    sampler = DockerStatsSampler(
        command=["docker", "stats", "container"],
        output_path=tmp_path / "resources.jsonl",
        popen=lambda *args, **kwargs: process,
        monotonic_clock=lambda: 1.0,
    )

    summary = sampler.stop()

    assert process.terminated is True
    assert summary["sample_count"] == 1
    assert summary["coverage"] == "exact"
    assert (tmp_path / "resources.jsonl").read_text(encoding="utf-8").count("\n") == 1
