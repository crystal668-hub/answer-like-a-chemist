#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime.agent_workspace import AttemptIdentity
from benchmarking.runtime.atomic_io import atomic_write_json
from benchmarking.runtime.cancellation import CancellationReason, CancellationToken
from benchmarking.runtime.container_runtime import ContainerAttemptSpec, DockerContainerRuntime
from benchmarking.runtime.observability import finish_runtime_metrics, start_runtime_metrics


CHILD = """import signal,time
def stop(*_):
    print('supervisor-finalized', flush=True)
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
time.sleep({sleep})
print('completed', flush=True)
"""


def run_one(*, mode: str, scenario: str, repeat: int, image: str) -> dict[str, object]:
    token = CancellationToken()
    runtime = DockerContainerRuntime(use_wait_client=mode == "managed")
    identity = AttemptIdentity(
        "docker-wait-acceptance", f"{mode}-{scenario}-{repeat}", "contract", "single_llm",
        "agent", scenario, repeat, f"session-{mode}-{scenario}-{repeat}", "phase5",
    )
    sleep_seconds = 0.25 if scenario == "complete" else 30
    handle = runtime.create(ContainerAttemptSpec(
        identity, image, ("python", "-c", CHILD.format(sleep=sleep_seconds)), network_mode="none"
    ))
    timer = None
    metrics = start_runtime_metrics()
    started = time.perf_counter()
    try:
        runtime.start(handle)
        if scenario == "cancel":
            timer = threading.Timer(0.35, lambda: token.cancel(CancellationReason(source="acceptance")))
            timer.start()
        result = runtime.collect(
            handle,
            timeout_seconds=0.35 if scenario == "timeout" else 10,
            cancellation_token=token,
        )
    finally:
        if timer is not None:
            timer.cancel()
        cleanup = runtime.remove(handle, force=True)
        snapshot = finish_runtime_metrics(metrics)
    return {
        "mode": mode,
        "scenario": scenario,
        "repeat": repeat,
        "wall_seconds": time.perf_counter() - started,
        "return_code": result.return_code,
        "timed_out": result.timed_out,
        "cancelled": result.cancelled,
        "stdout": result.stdout,
        "termination": result.cleanup.get("termination"),
        "removed": cleanup.removed,
        "metrics": snapshot,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="python:3.12-slim-bookworm")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for scenario in ("complete", "timeout", "cancel"):
        for repeat in range(args.repeats):
            modes = ("legacy", "managed") if repeat % 2 == 0 else ("managed", "legacy")
            for mode in modes:
                row = run_one(mode=mode, scenario=scenario, repeat=repeat + 1, image=args.image)
                rows.append(row)
                atomic_write_json(output / "raw-results.json", rows)
    audits = []
    for row in rows:
        expected_flag = row["scenario"]
        valid = row["removed"] is True
        if expected_flag == "complete":
            valid = valid and row["return_code"] == 0 and "completed" in row["stdout"]
        elif expected_flag == "timeout":
            valid = valid and row["timed_out"] is True and "supervisor-finalized" in row["stdout"]
        else:
            valid = valid and row["cancelled"] is True and "supervisor-finalized" in row["stdout"]
        audits.append({"mode": row["mode"], "scenario": row["scenario"], "repeat": row["repeat"],
                       "status": "passed" if valid else "failed"})
    summary = {}
    for scenario in ("complete", "timeout", "cancel"):
        summary[scenario] = {}
        for mode in ("legacy", "managed"):
            selected = [row for row in rows if row["scenario"] == scenario and row["mode"] == mode]
            summary[scenario][mode] = {
                "wall_seconds": statistics.median(row["wall_seconds"] for row in selected),
                "docker_command_count": statistics.median(row["metrics"]["counters"]["docker_command_count"] for row in selected),
                "docker_wait_count": statistics.median(row["metrics"]["counters"].get("docker_command_count.wait", 0) for row in selected),
                "docker_wait_timeout_count": statistics.median(row["metrics"]["counters"].get("docker_command_timeout_count", 0) for row in selected),
            }
    report = {"schema_version": 1, "status": "passed" if all(a["status"] == "passed" for a in audits) else "failed",
              "image": args.image, "repeats": args.repeats, "summary": summary, "audits": audits}
    atomic_write_json(output / "report.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
