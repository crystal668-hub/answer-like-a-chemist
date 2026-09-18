from __future__ import annotations

import json
from pathlib import Path

from benchmarking.runtime.attempt_observability import (
    aggregate_attempt_observability,
    attempt_artifact_paths,
    legacy_observability,
    package_delta,
    package_summary,
    summarize_tool_events,
    token_usage_from_trajectory,
)
from benchmarking.runtime.transcript_index import TranscriptIndex


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_tool_summary_uses_structured_status_and_redacts_command(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "message": {
                    "role": "assistant",
                    "timestamp": 1000,
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "blocked",
                            "name": "exec",
                            "arguments": {
                                "command": "TOKEN=secret uv pip install rdkit",
                                "workdir": "/private/workspace",
                            },
                        }
                    ],
                }
            },
            {
                "message": {
                    "role": "toolResult",
                    "timestamp": 1100,
                    "toolCallId": "blocked",
                    "toolName": "exec",
                    "isError": True,
                    "details": {"status": "blocked"},
                    "content": [{"type": "text", "text": "benchmark_workspace_guard_blocked"}],
                }
            },
        ],
    )

    summary = summarize_tool_events(
        TranscriptIndex.from_path(transcript),
        path_replacements={"/private/workspace": "$BENCHMARK_WORKSPACE_DIR"},
    )

    assert summary["failure_count"] == 1
    assert summary["status_counts"] == {"blocked": 1}
    assert summary["exec_calls"][0]["command"] == "TOKEN=<redacted> uv pip install rdkit"
    assert summary["exec_calls"][0]["requested_cwd"] == "$BENCHMARK_WORKSPACE_DIR"
    assert summary["exec_calls"][0]["duration_ms"] == 100


def test_tool_summary_resolves_background_process_terminal_result(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    _write_jsonl(
        transcript,
        [
            {"message": {"role": "assistant", "timestamp": 1000, "content": [{"type": "toolCall", "id": "exec", "name": "exec", "arguments": {"command": "uv pip install rdkit"}}]}},
            {"message": {"role": "toolResult", "timestamp": 1100, "toolCallId": "exec", "toolName": "exec", "details": {"status": "running"}, "content": [{"type": "text", "text": "Command still running (session calm-river, pid 3)."}]}},
            {"message": {"role": "assistant", "timestamp": 2000, "content": [{"type": "toolCall", "id": "poll", "name": "process", "arguments": {"action": "poll", "sessionId": "calm-river"}}]}},
            {"message": {"role": "toolResult", "timestamp": 2400, "toolCallId": "poll", "toolName": "process", "isError": True, "details": {"status": "failed", "exitCode": 2}, "content": [{"type": "text", "text": "Process exited with code 2."}]}},
        ],
    )

    summary = summarize_tool_events(TranscriptIndex.from_path(transcript))

    assert summary["exec_failure_count"] == 1
    assert summary["exec_calls"][0]["status"] == "failure"
    assert summary["exec_calls"][0]["exit_code"] == 2
    assert summary["exec_calls"][0]["duration_ms"] == 1400


def test_token_usage_prefers_final_trajectory_artifact_and_validates_total(tmp_path: Path) -> None:
    trajectory = tmp_path / "session.trajectory.jsonl"
    _write_jsonl(
        trajectory,
        [
            {"type": "trace.artifacts", "data": {"usage": {"input": 10, "output": 4, "cacheRead": 20, "reasoningTokens": 3, "total": 34}}},
        ],
    )

    usage = token_usage_from_trajectory(trajectory)

    assert usage["total_tokens"] == 34
    assert usage["reasoning_tokens"] == 3
    assert usage["provider_total_consistent"] is True
    assert usage["source"] == "trajectory_final"


def test_package_delta_distinguishes_added_changed_and_policy_removed() -> None:
    delta = package_delta(
        [{"name": "pip", "version": "1"}, {"name": "base", "version": "1"}],
        [{"name": "pip", "version": "2"}, {"name": "rdkit", "version": "3"}],
        [{"name": "pip", "version": "2"}],
    )

    assert delta["added"] == [{"name": "rdkit", "version": "3"}]
    assert delta["removed"] == [{"name": "base", "version": "1"}]
    assert delta["version_changed"] == [{"name": "pip", "from_version": "1", "to_version": "2"}]
    assert delta["policy_removed"] == [{"name": "rdkit", "version": "3"}]


def test_package_summary_separates_direct_and_transitive_additions() -> None:
    summary = package_summary(
        {
            "baseline_distributions": [{"name": "pip", "version": "1"}],
            "distributions": [
                {"name": "pip", "version": "1"},
                {"name": "rdkit", "version": "2"},
                {"name": "numpy", "version": "3"},
            ],
            "effective_distributions": [
                {"name": "pip", "version": "1"},
                {"name": "rdkit", "version": "2"},
                {"name": "numpy", "version": "3"},
            ],
            "install_events": [
                {"command": "cd scratch && uv pip install rdkit", "outcome": "succeeded"}
            ],
        }
    )

    assert summary["direct_requested_packages"] == ["rdkit"]
    assert summary["direct_added_packages"] == ["rdkit"]
    assert summary["transitive_added_packages"] == ["numpy"]


def test_attempt_aggregation_includes_retries_and_does_not_double_count_reasoning() -> None:
    attempts = [
        {
            "attempt_index": 0,
            "status": "failed",
            "coverage": {key: "exact" for key in ("tools", "packages", "tokens", "timing", "resources")},
            "timing": {"attempt_wall_seconds": 5, "agent_seconds": 4},
            "tokens": {"input_tokens": 10, "output_tokens": 4, "cache_read_tokens": 20, "cache_write_tokens": 0, "reasoning_tokens": 3, "total_tokens": 34, "invocation_count": 2},
            "tools": {"call_count": 2, "failure_count": 1, "exec_call_count": 1, "exec_failure_count": 1, "status_counts": {"success": 1, "failure": 1}},
            "packages": {"install_event_count": 1, "failed_install_event_count": 1, "delta": {"added": []}},
            "resources": {"sample_count": 2, "cpu_peak_percent": 30, "memory_peak_bytes": 100, "pids_peak": 2},
        },
        {
            "attempt_index": 1,
            "status": "completed",
            "coverage": {key: "exact" for key in ("tools", "packages", "tokens", "timing", "resources")},
            "timing": {"attempt_wall_seconds": 7, "agent_seconds": 6},
            "tokens": {"input_tokens": 5, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 1, "total_tokens": 7, "invocation_count": 1},
            "tools": {"call_count": 1, "failure_count": 0, "exec_call_count": 1, "exec_failure_count": 0, "status_counts": {"success": 1}},
            "packages": {"install_event_count": 0, "failed_install_event_count": 0, "delta": {"added": [{"name": "rdkit", "version": "1"}]}},
            "resources": {"sample_count": 3, "cpu_peak_percent": 40, "memory_peak_bytes": 200, "pids_peak": 3},
        },
    ]

    result = aggregate_attempt_observability(attempts, record_wall_seconds=16, retry_backoff_seconds=2, scoring_seconds=1)

    assert result["totals"]["tokens"]["total_tokens"] == 41
    assert result["totals"]["tokens"]["reasoning_tokens"] == 4
    assert result["totals"]["tokens"]["invocation_count"] == 3
    assert result["totals"]["timing"]["attempt_wall_seconds"] == 12
    assert result["totals"]["tools"]["failure_count"] == 1
    assert result["totals"]["resources"]["memory_peak_bytes"] == 200
    assert result["final_attempt"]["attempt_index"] == 1


def test_legacy_projection_does_not_fabricate_missing_timing_or_zero_failures() -> None:
    result = legacy_observability(
        {
            "runner_meta": {
                "skill_use_audit": {
                    "openclaw_tool_call_count": 5,
                    "tool_failure_count": 0,
                    "tool_result_error_count": 2,
                },
                "timeout_retry": {"attempts": 3},
            }
        }
    )

    assert result["coverage"]["timing"] == "unavailable"
    assert result["totals"]["timing"] == {}
    assert result["totals"]["tools"]["failure_count"] == 2
    assert result["attempt_count"] == 3


def test_artifact_paths_hash_normalized_slug_collisions(tmp_path: Path) -> None:
    first = attempt_artifact_paths(
        tmp_path,
        {"group_id": "g", "record_id": "a/b", "attempt_index": 0, "session_id": "s"},
    )
    second = attempt_artifact_paths(
        tmp_path,
        {"group_id": "g", "record_id": "a?b", "attempt_index": 0, "session_id": "s"},
    )

    assert first["root"] != second["root"]
