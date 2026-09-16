from __future__ import annotations

import gc
import json
import subprocess
import weakref
from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarking.runtime import vgb_bridge
from benchmarking.runtime.atomic_io import atomic_write_json
from benchmarking.runtime.attempt_finalization import write_evidence
from benchmarking.runtime.container_runtime import DockerContainerRuntime
from benchmarking.runtime.observability import (
    decode_transcript_json,
    finish_runtime_metrics,
    observe_transcript_text,
    observed_duration,
    start_runtime_metrics,
)


FIXTURE = Path(__file__).parent / "fixtures/runtime_observability/scenario.json"


def test_deterministic_fixture_covers_required_runtime_scenarios() -> None:
    scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))

    assert {group["skills_enabled"] for group in scenario["groups"]} == {True, False}
    assert {record["outcome"] for record in scenario["records"]} == {
        "scored",
        "completed",
        "failed",
        "cancelled",
    }
    assert any(record.get("attempts") == 2 for record in scenario["records"])
    assert any(record.get("vgb_processes") == 1 for record in scenario["records"])
    assert scenario["large_transcript"]["repeat_count"] >= 1000


def test_metrics_count_writes_transcript_decodes_commands_and_processes(tmp_path: Path) -> None:
    metrics = start_runtime_metrics()
    try:
        atomic_write_json(tmp_path / "per-record/g/记录.json", {"answer": "甲"})
        write_evidence(tmp_path / "scoring-pending/g/r.json", {"status": "ready"})

        scenario = json.loads(FIXTURE.read_text(encoding="utf-8"))
        transcript = scenario["large_transcript"]["line"] * scenario["large_transcript"]["repeat_count"]
        observe_transcript_text(transcript)
        decode_transcript_json(scenario["large_transcript"]["line"])
        with pytest.raises(json.JSONDecodeError):
            decode_transcript_json("{truncated")

        docker_calls: list[list[str]] = []

        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            docker_calls.append(command)
            return subprocess.CompletedProcess(command, 0, "{}", "")

        DockerContainerRuntime(docker_executable="docker", run_subprocess=run).check_ready()

        config = vgb_bridge.load_release_config()
        completed = subprocess.CompletedProcess([], 0, '{"status":"scored"}', "")
        with (
            patch.object(Path, "is_file", return_value=True),
            patch.object(vgb_bridge.subprocess, "run", return_value=completed),
        ):
            result = vgb_bridge._invoke_api(
                config,
                {"action": "evaluate_one"},
                timeout=1,
                require_manifest=False,
            )
        assert result == {"status": "scored"}
        assert docker_calls and docker_calls[0][1] == "version"
    finally:
        snapshot = finish_runtime_metrics(metrics)

    counters = snapshot["counters"]
    assert counters["state_write_count"] == 2
    assert counters["state_write_count.per_record"] == 1
    assert counters["state_write_count.scoring_pending"] == 1
    assert counters["transcript_read_count"] == 1
    assert counters["transcript_lines"] == 1000
    assert counters["transcript_json_decode_count"] == 2
    assert counters["transcript_json_decode_error_count"] == 1
    assert counters["docker_command_count"] == 1
    assert counters["vgb_process_count"] == 1
    assert snapshot["peak_rss_bytes"] > 0


def test_duration_decorator_does_not_retain_arguments() -> None:
    class Payload:
        pass

    @observed_duration("fixture_stage")
    def consume(value: Payload) -> None:
        assert value is not None

    metrics = start_runtime_metrics()
    value = Payload()
    reference = weakref.ref(value)
    consume(value)
    del value
    gc.collect()
    snapshot = finish_runtime_metrics(metrics)

    assert reference() is None
    assert snapshot["durations"]["fixture_stage"]["count"] == 1
    assert snapshot["durations"]["invocation"]["count"] == 1
