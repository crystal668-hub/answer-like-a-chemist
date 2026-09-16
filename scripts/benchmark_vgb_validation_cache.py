#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime.atomic_io import atomic_write_json, atomic_write_text
from benchmarking.runtime.vgb_bridge import InvocationValidationCache, load_release_config, validate_runtime_files


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def measure(mode: str, iterations: int) -> dict[str, object]:
    config = load_release_config()
    cache = InvocationValidationCache() if mode == "cached" else None
    started = time.perf_counter()
    manifest = None
    for _ in range(iterations):
        manifest = validate_runtime_files(config, validation_cache=cache)
    elapsed = time.perf_counter() - started
    return {
        "mode": mode,
        "iterations": iterations,
        "wall_seconds": elapsed,
        "peak_rss_bytes": peak_rss_bytes(),
        "manifest": manifest,
        "cache": cache.to_meta() if cache is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("cached", "uncached"))
    args = parser.parse_args()
    if args.iterations < 1 or args.repeats < 1:
        parser.error("iterations and repeats must be positive")
    if args.mode:
        print(json.dumps(measure(args.mode, args.iterations), ensure_ascii=False, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required for a matrix run")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    raw = []
    for repeat in range(args.repeats):
        modes = ("uncached", "cached") if repeat % 2 == 0 else ("cached", "uncached")
        for mode in modes:
            command = [sys.executable, str(Path(__file__).resolve()), "--mode", mode,
                       "--iterations", str(args.iterations)]
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            entry = {"repeat": repeat + 1, "mode": mode, "command": command,
                     "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
            raw.append(entry)
            atomic_write_text(output / "raw-results.jsonl",
                              "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in raw))
            if completed.returncode:
                return completed.returncode
            row = json.loads(completed.stdout)
            row["repeat"] = repeat + 1
            rows.append(row)
    manifests = {json.dumps(row["manifest"], sort_keys=True) for row in rows}
    report = {
        "schema_version": 1,
        "status": "passed" if len(manifests) == 1 else "failed",
        "iterations": args.iterations,
        "repeats": args.repeats,
        "python": sys.version,
        "platform": platform.platform(),
        "measurements": rows,
        "medians": {
            mode: {
                key: statistics.median(row[key] for row in rows if row["mode"] == mode)
                for key in ("wall_seconds", "peak_rss_bytes")
            }
            for mode in ("uncached", "cached")
        },
    }
    atomic_write_json(output / "report.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
