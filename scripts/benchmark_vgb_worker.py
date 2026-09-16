"""Offline old/new verifier shadow and transport benchmark; no model calls.

Default: install a deterministic fixture into a private venv. For pinned-release
acceptance supply --release-config and --requests (JSONL track/task_id/answer_text).
Each mode/repetition runs in its own measurement process for comparable RSS.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import resource
import statistics
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime import vgb_bridge as bridge
from benchmarking.runtime.atomic_io import atomic_write_json, atomic_write_text
from benchmarking.runtime.cancellation import CancellationToken, OwnedProcessRegistry
from benchmarking.runtime.observability import start_runtime_metrics, finish_runtime_metrics
from benchmarking.runtime.vgb_worker import VerifierWorker


def fixture_config(root: Path, *, install: bool = False) -> bridge.ReleaseConfig:
    bridge.runtime_paths.data_root = root / "data"
    bridge.runtime_paths.project_state_root = root / "state"
    content = (ROOT / "tests/fixtures/vgb_worker/verifier_grounded_benchmark.py").read_bytes()
    config = bridge.ReleaseConfig("fixture", "1", "commit", "tag", "fixture.whl",
        hashlib.sha256(content).hexdigest(), len(content),
        {track: {"task_ids": ["task-a", "task-b"], "timeout_seconds": 10} for track in ("rdkit", "property")})
    if install:
        config.wheel_path.parent.mkdir(parents=True, exist_ok=True)
        config.wheel_path.write_bytes(content)
        venv.EnvBuilder(with_pip=False, symlinks=True).create(config.runtime_root / ".venv")
        site = config.runtime_root / f".venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        (site / "verifier_grounded_benchmark.py").write_bytes(content)
        atomic_write_json(config.runtime_manifest, {**config.identity, "source_commit": config.source_commit,
            "source_tag": config.source_tag, "wheel_path": str(config.wheel_path)})
    return config


def requests(args):
    if args.requests:
        loaded = []
        repeatable = []
        with Path(args.requests).open(encoding="utf-8") as handle:
            for line in handle:
                request = json.loads(line)
                should_repeat = bool(request.pop("repeatable", False))
                result_path = request.pop("answer_result_path", None)
                if result_path:
                    payload = json.loads(Path(result_path).expanduser().resolve().read_text(encoding="utf-8"))
                    request["answer_text"] = str(payload.get("answer_text") or payload.get("full_response_text") or "")
                loaded.append(request)
                if should_repeat:
                    repeatable.append(request)
        if args.cycle_requests_to:
            yield from loaded[: args.cycle_requests_to]
            remaining = max(0, args.cycle_requests_to - len(loaded))
            yield from itertools.islice(itertools.cycle(repeatable or loaded), remaining)
        else:
            yield from loaded
    else:
        answers = ["FINAL ANSWER: CCO", "invalid", "FINAL ANSWER: 1.25", "infrastructure", "FINAL ANSWER: CCO"]
        for index in range(args.records):
            yield {"track": "rdkit" if index % 2 == 0 else "property",
                   "task_id": "task-a" if index % 3 else "task-b", "answer_text": answers[index % len(answers)]}


def rss(who):
    value = resource.getrusage(who).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def measure_mode(args):
    cwd_before = os.getcwd()
    environment_before = hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).hexdigest()
    config = bridge.load_release_config(Path(args.release_config)) if args.release_config else fixture_config(Path(args.output) / "fixture")
    run_root = Path(args.output) / f"{args.mode}-{args.repeat}"
    run_root.mkdir(parents=True, exist_ok=False)
    token = CancellationToken()
    cache = bridge.InvocationValidationCache()
    worker = VerifierWorker(config, evidence_root=run_root / "worker", cancellation_token=token,
        process_registry=OwnedProcessRegistry(cancellation_token=token), validation_cache=cache) if args.mode == "worker" else None
    metrics = start_runtime_metrics()
    durations = []
    input_digest = hashlib.sha256()
    digest = hashlib.sha256()
    output_bytes = 0
    started = time.perf_counter()
    try:
        bridge.validate_runtime_files(config, validation_cache=cache)
        with (run_root / "results.jsonl").open("wb") as handle:
            for request in requests(args):
                input_digest.update(json.dumps(request, sort_keys=True).encode())
                tick = time.perf_counter()
                result = bridge.evaluate_answer(**request, release_config=config, release_identity=config.identity,
                                               validation_cache=cache, worker=worker)
                durations.append(time.perf_counter() - tick)
                encoded = (json.dumps(result, sort_keys=True, ensure_ascii=False) + "\n").encode()
                handle.write(encoded)
                output_bytes += len(encoded)
                digest.update(encoded)
    finally:
        if worker is not None:
            worker.close()
        snapshot = finish_runtime_metrics(metrics)
        atomic_write_json(run_root / "runtime-metrics.json", snapshot)
    report = {"mode": args.mode, "records": len(durations), "release": config.identity,
        "wall_seconds": time.perf_counter() - started,
        "first_request_seconds": durations[0] if durations else None,
        "median_request_seconds": statistics.median(durations) if durations else None,
        "parent_peak_rss_bytes": rss(resource.RUSAGE_SELF),
        "largest_reaped_child_peak_rss_bytes": rss(resource.RUSAGE_CHILDREN),
        "process_count": snapshot["counters"].get("vgb_process_count", 0),
        "input_sha256": input_digest.hexdigest(), "output_sha256": digest.hexdigest(), "output_bytes": output_bytes,
        "worker": worker.to_meta() if worker else None,
        "process_cwd_before": cwd_before, "process_cwd_after": os.getcwd(),
        "environment_sha256_before": environment_before,
        "environment_sha256_after": hashlib.sha256(json.dumps(dict(os.environ), sort_keys=True).encode()).hexdigest()}
    atomic_write_json(run_root / "measurement.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=120)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--release-config")
    parser.add_argument("--requests")
    parser.add_argument("--cycle-requests-to", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / "state/benchmark-runs/temporary/vgb-worker/offline" /
                        f"vgb-worker-offline-{time.strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--mode", choices=("isolated", "worker"))
    parser.add_argument("--repeat", type=int, default=0)
    args = parser.parse_args()
    if bool(args.release_config) != bool(args.requests):
        parser.error("--release-config and --requests must be supplied together")
    if args.records < 1 or args.repeats < 1:
        parser.error("record and repeat counts must be positive")
    if args.cycle_requests_to is not None and args.cycle_requests_to < 1:
        parser.error("--cycle-requests-to must be positive")
    if args.mode:
        measure_mode(args)
        return 0
    args.output.mkdir(parents=True, exist_ok=False)
    if not args.release_config:
        fixture_config(args.output / "fixture", install=True)
    resolved_requests = args.requests
    if args.requests:
        resolved = list(requests(args))
        resolved_requests_path = args.output / "prepared-requests.jsonl"
        atomic_write_text(
            resolved_requests_path,
            "".join(json.dumps(request, ensure_ascii=False, sort_keys=True) + "\n" for request in resolved),
        )
        resolved_requests = str(resolved_requests_path)
    measurements = []
    for repeat in range(args.repeats):
        # Alternate execution order to reduce warm filesystem bias.
        for mode in (("isolated", "worker") if repeat % 2 == 0 else ("worker", "isolated")):
            command = [sys.executable, str(Path(__file__).resolve()), "--mode", mode, "--repeat", str(repeat),
                       "--output", str(args.output), "--records", str(args.records)]
            if args.release_config:
                command += ["--release-config", args.release_config, "--requests", resolved_requests]
            subprocess.run(command, check=True)
            measurements.append(json.loads((args.output / f"{mode}-{repeat}/measurement.json").read_text()))
    signatures = {(m["input_sha256"], m["output_sha256"], m["output_bytes"], m["records"]) for m in measurements}
    equal = len(signatures) == 1
    field_audits = []
    if args.release_config:
        for repeat in range(args.repeats):
            isolated = [json.loads(line) for line in (args.output / f"isolated-{repeat}/results.jsonl").read_text().splitlines()]
            worker = [json.loads(line) for line in (args.output / f"worker-{repeat}/results.jsonl").read_text().splitlines()]
            for index, (old, new) in enumerate(zip(isolated, worker, strict=True), start=1):
                differences = {
                    key: {"isolated": old.get(key), "worker": new.get(key)}
                    for key in sorted(set(old) | set(new))
                    if old.get(key) != new.get(key)
                }
                field_audits.append({"repeat": repeat + 1, "request": index,
                    "status": "passed" if not differences else "failed", "differences": differences})
        equal = equal and all(item["status"] == "passed" for item in field_audits)
    report = {"kind": "pinned_release" if args.release_config else "offline_fixture", "equivalent": equal,
        "measurements": measurements, "field_audit": field_audits, "medians": {}}
    for mode in ("isolated", "worker"):
        selected = [m for m in measurements if m["mode"] == mode]
        report["medians"][mode] = {key: statistics.median(m[key] for m in selected) for key in (
            "wall_seconds", "first_request_seconds", "median_request_seconds", "parent_peak_rss_bytes",
            "largest_reaped_child_peak_rss_bytes", "process_count", "output_bytes")}
    atomic_write_json(args.output / "shadow-report.json", report)
    print(json.dumps({"output": str(args.output), "equivalent": equal, "medians": report["medians"]}, indent=2))
    return 0 if equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
