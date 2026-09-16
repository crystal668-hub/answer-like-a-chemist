#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarking.runtime.atomic_io import atomic_write_json, atomic_write_text


DEFAULT_SCENARIOS = ((1_000, 0), (10_000, 0), (1_000, 65_536), (10_000, 16_384))


def parse_scenario(value: str) -> tuple[int, int]:
    try:
        records_text, payload_text = value.split(":", 1)
        records, payload_bytes = int(records_text), int(payload_text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("scenario must be RECORDS:PAYLOAD_BYTES") from exc
    if records <= 0 or payload_bytes < 0:
        raise argparse.ArgumentTypeError("scenario values must be positive records and non-negative bytes")
    return records, payload_bytes


def git_output(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for records, payload_bytes in sorted({(row["records"], row["payload_bytes"]) for row in rows}):
        key = f"{records}:{payload_bytes}"
        scenario_rows = [
            row for row in rows
            if row["records"] == records and row["payload_bytes"] == payload_bytes
        ]
        modes: dict[str, Any] = {}
        for mode in ("legacy", "streaming"):
            selected = [row for row in scenario_rows if row["mode"] == mode]
            modes[mode] = {
                metric: statistics.median(float(row[metric]) for row in selected)
                for metric in (
                    "peak_rss_bytes", "wall_seconds", "persistence_seconds",
                    "aggregation_seconds", "serialization_seconds", "results_json_bytes",
                )
            }
        report[key] = {"records": records, "payload_bytes": payload_bytes, "modes": modes}
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenario", action="append", type=parse_scenario)
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error("--repeats must be at least 2")
    scenarios = tuple(args.scenario or DEFAULT_SCENARIOS)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    commands: list[list[str]] = []
    raw_rows: list[dict[str, Any]] = []
    raw_evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    for records, payload_bytes in scenarios:
        for repeat in range(args.repeats):
            modes = ("legacy", "streaming") if repeat % 2 == 0 else ("streaming", "legacy")
            pair: dict[str, dict[str, Any]] = {}
            for mode in modes:
                command = [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "benchmark_runtime_baseline.py"),
                    "--records", str(records),
                    "--payload-bytes", str(payload_bytes),
                    "--mode", mode,
                ]
                commands.append(command)
                completed = subprocess.run(
                    command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False
                )
                raw_entry: dict[str, Any] = {
                    "records": records,
                    "payload_bytes": payload_bytes,
                    "repeat": repeat + 1,
                    "mode": mode,
                    "returncode": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
                if completed.returncode == 0:
                    try:
                        metrics = json.loads(completed.stdout)
                    except json.JSONDecodeError as exc:
                        raw_entry["parse_error"] = str(exc)
                    else:
                        raw_entry["metrics"] = metrics
                        pair[mode] = metrics
                        raw_rows.append({**metrics, "repeat": repeat + 1})
                raw_evidence.append(raw_entry)
                atomic_write_text(
                    output_dir / "raw-results.jsonl",
                    "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in raw_evidence),
                )
                if completed.returncode != 0 or "metrics" not in raw_entry:
                    atomic_write_json(output_dir / "audit-findings.json", [*findings, raw_entry])
                    return 1
            mismatch = {
                key: {"legacy": pair["legacy"].get(key), "streaming": pair["streaming"].get(key)}
                for key in ("group_order", "summary_bytes", "results_json_bytes", "results_sha256", "retained_fields")
                if pair["legacy"].get(key) != pair["streaming"].get(key)
            }
            findings.append({
                "kind": "pair_audit",
                "records": records,
                "payload_bytes": payload_bytes,
                "repeat": repeat + 1,
                "status": "passed" if not mismatch else "failed",
                "mismatches": mismatch,
            })
            if mismatch:
                atomic_write_json(output_dir / "audit-findings.json", findings)
                return 1

    runner_meta = {
        "schema_version": 1,
        "started_at": started_at,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "project_root": str(PROJECT_ROOT),
        "git_head": git_output("rev-parse", "HEAD"),
        "git_status_short": git_output("status", "--short"),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "repeats": args.repeats,
        "scenarios": [{"records": records, "payload_bytes": payload} for records, payload in scenarios],
        "commands": commands,
        "measurement_scope": "per-record atomic persistence, aggregation, and atomic complete results.json persistence",
    }
    report = {
        "schema_version": 1,
        "status": "passed",
        "runner_meta": "runner-meta.json",
        "raw_results": "raw-results.jsonl",
        "audit_findings": "audit-findings.json",
        "summary": summarize(raw_rows),
    }
    atomic_write_json(output_dir / "runner-meta.json", runner_meta)
    atomic_write_json(output_dir / "audit-findings.json", findings)
    atomic_write_json(output_dir / "report.json", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
