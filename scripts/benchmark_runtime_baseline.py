#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
import sys
import time

from benchmarking.core.reporting import GroupRecordResult, aggregate_results


def build_result(index: int, *, payload_bytes: int) -> GroupRecordResult:
    detail = (f"record-{index}:" + "x" * payload_bytes)[:payload_bytes]
    return GroupRecordResult(
        schema_version=3,
        group_id="single_llm_skills_on" if index % 2 == 0 else "single_llm_skills_off",
        group_label="fixture",
        runner="single_llm",
        websearch=False,
        record_id=f"record-{index:06d}",
        subset=f"subset-{index % 4}",
        dataset="runtime-baseline",
        source_file="fixture.jsonl",
        eval_kind="generic_semantic",
        prompt=detail,
        reference_answer="expected",
        answer_text=detail,
        evaluation={
            "passed": index % 3 != 0,
            "score": float(index % 3 != 0),
            "normalized_score": float(index % 3 != 0),
            "details": {},
        },
        runner_meta={"fixture_detail": detail},
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
    )


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, required=True)
    parser.add_argument("--payload-bytes", type=int, default=0)
    args = parser.parse_args()
    started = time.perf_counter()
    results = [build_result(index, payload_bytes=args.payload_bytes) for index in range(args.records)]
    generated = time.perf_counter()
    summary = aggregate_results(results)
    aggregated = time.perf_counter()
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True).encode("utf-8")
    finished = time.perf_counter()
    print(
        json.dumps(
            {
                "records": args.records,
                "payload_bytes": args.payload_bytes,
                "generation_seconds": generated - started,
                "aggregation_seconds": aggregated - generated,
                "serialization_seconds": finished - aggregated,
                "summary_bytes": len(encoded),
                "peak_rss_bytes": peak_rss_bytes(),
                "group_order": summary["group_order"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
