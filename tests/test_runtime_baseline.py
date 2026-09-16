import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run_mode(mode: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "benchmark_runtime_baseline.py"),
            "--records", "12",
            "--payload-bytes", "256",
            "--mode", mode,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_runtime_baseline_modes_measure_equivalent_persistence_work():
    legacy = _run_mode("legacy")
    streaming = _run_mode("streaming")
    for key in (
        "group_order", "summary_bytes", "results_json_bytes", "results_sha256", "retained_fields"
    ):
        assert streaming[key] == legacy[key]
    assert legacy["retained_fields"] == [
        "raw", "runner_meta", "workspace_isolation.findings", "full_response_text"
    ]
    for payload in (legacy, streaming):
        assert payload["persistence_seconds"] >= 0
        assert payload["aggregation_seconds"] >= 0
        assert payload["serialization_seconds"] >= 0
        assert payload["wall_seconds"] >= 0
