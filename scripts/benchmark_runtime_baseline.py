#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarking.core.reporting import GroupRecordResult, aggregate_results
from benchmarking.runtime.atomic_io import atomic_write_json
from benchmarking.workflow.run_state import (
    iter_results_from_output_root,
    write_results_json_stream,
)


def build_result(index: int, *, payload_bytes: int) -> GroupRecordResult:
    prefix = f"record-{index}:"
    detail = (prefix + chr(65 + index % 26) * payload_bytes)[:payload_bytes]
    return GroupRecordResult(
        schema_version=4,
        group_id="single_llm_skills_on" if index % 2 == 0 else "single_llm_skills_off",
        group_label="fixture",
        runner="single_llm",
        websearch=False,
        record_id=f"record-{index:06d}",
        track=("rdkit", "xtb", "property_calculation_advanced", "property_calculation_basic")[index % 4],
        source_file="fixture.jsonl",
        eval_kind="verifier_grounded",
        prompt=detail,
        reference_answer="expected",
        answer_text=detail,
        evaluation={
            "passed": index % 3 != 0,
            "score": float(index % 3 != 0),
            "normalized_score": float(index % 3 != 0),
            "details": {},
        },
        runner_meta={
            "fixture_detail": detail,
            "workspace_isolation": {
                "preflight_ok": True,
                "audit_execution_status": "complete",
                "boundary_status": "clean",
                "contamination_status": "clear",
                "adjudication": "scoreable",
                "archive_ok": True,
                "findings": [{"code": "fixture", "record": index}],
            },
        },
        raw={"fixture_detail": detail},
        elapsed_seconds=float(index % 7),
        run_lifecycle_status="completed",
        protocol_completion_status="completed",
        protocol_acceptance_status=None,
        answer_availability="native_final",
        answer_reliability="native",
        evaluable=True,
        scored=True,
        recovery_mode="none",
        degraded_execution=False,
        skills_enabled=index % 2 == 0,
        short_answer_text=f"answer-{index}",
        full_response_text=detail,
    )


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, required=True)
    parser.add_argument("--payload-bytes", type=int, default=0)
    parser.add_argument("--mode", choices=("legacy", "streaming"), required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="runtime-baseline-") as temp_dir:
        root = Path(temp_dir)
        paths: list[Path] = []
        results: list[GroupRecordResult] = []
        for index in range(args.records):
            item = build_result(index, payload_bytes=args.payload_bytes)
            path = root / "per-record" / item.group_id / f"{item.record_id}.json"
            atomic_write_json(path, asdict(item))
            paths.append(path)
            if args.mode == "legacy":
                results.append(item)
        persisted = time.perf_counter()
        group_ids = ["single_llm_skills_on", "single_llm_skills_off"]
        ordered_paths = [
            path
            for group_id in group_ids
            for path in sorted(root.joinpath("per-record", group_id).glob("*.json"))
        ]
        if args.mode == "legacy":
            ordered_results = sorted(results, key=lambda item: (group_ids.index(item.group_id), item.record_id))
            summary = aggregate_results(ordered_results)
        else:
            summary = aggregate_results(iter_results_from_output_root(root, group_ids=group_ids))
        aggregated = time.perf_counter()
        output_path = root / "results.json"
        if args.mode == "legacy":
            atomic_write_json(output_path, {"summary": summary, "results": [asdict(item) for item in ordered_results]})
        else:
            write_results_json_stream(output_path, {"summary": summary}, ordered_paths)
        finished = time.perf_counter()
        print(json.dumps({
            "mode": args.mode,
            "records": args.records,
            "payload_bytes": args.payload_bytes,
            "persistence_seconds": persisted - started,
            "aggregation_seconds": aggregated - persisted,
            "serialization_seconds": finished - aggregated,
            "wall_seconds": finished - started,
            "summary_bytes": len(json.dumps(summary, ensure_ascii=False).encode()),
            "results_json_bytes": output_path.stat().st_size,
            "results_sha256": file_sha256(output_path),
            "peak_rss_bytes": peak_rss_bytes(),
            "group_order": summary["group_order"],
            "retained_fields": ["raw", "runner_meta", "workspace_isolation.findings", "full_response_text"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
