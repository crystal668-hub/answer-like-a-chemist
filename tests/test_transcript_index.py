from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarking.core.convergence import (
    extract_latest_complete_answer_from_transcript_for_eval,
    summarize_transcript_convergence,
)
from benchmarking.runtime.attempt_environment import dependency_install_events
from benchmarking.runtime.observability import finish_runtime_metrics, start_runtime_metrics
from benchmarking.runtime.transcript_index import TranscriptIndex, TranscriptIndexError


def write_rows(path: Path, rows: list[dict[str, object]], *, truncated: bool = False) -> None:
    content = "".join(json.dumps(row) + "\n" for row in rows)
    if truncated:
        content += '{"message":'
    path.write_text(content, encoding="utf-8")


def test_shared_index_serves_convergence_answer_and_dependency_views_once(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    rows = [
        {
            "customType": "openclaw:prompt-error",
            "data": {"error": "historical timeout"},
        },
        {
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "toolCall",
                    "id": "install",
                    "name": "exec",
                    "arguments": {"command": "uv pip install rdkit"},
                }],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "install",
                "toolName": "exec",
                "content": [{"type": "text", "text": "Process exited with code 0."}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "FINAL ANSWER: indexed"}],
            }
        },
    ]
    write_rows(transcript, rows)
    metrics = start_runtime_metrics()
    try:
        index = TranscriptIndex.from_path(transcript)
        summary = summarize_transcript_convergence(transcript, transcript_index=index)
        answer = extract_latest_complete_answer_from_transcript_for_eval(
            transcript,
            transcript_index=index,
        )
        installs = dependency_install_events(transcript, transcript_index=index)
    finally:
        snapshot = finish_runtime_metrics(metrics)

    assert summary["assistant_turn_count"] == 2
    assert summary["prompt_error_count"] == 1
    assert answer == "FINAL ANSWER: indexed"
    assert installs == [{
        "tool_call_id": "install",
        "command": "uv pip install rdkit",
        "call_line": 2,
        "result_line": 3,
        "outcome": "succeeded",
    }]
    assert snapshot["counters"]["transcript_read_count"] == 1
    assert snapshot["counters"]["transcript_json_decode_count"] == len(rows)


def test_truncated_line_is_ignored_by_recovery_views_but_rejected_by_strict_audit_view(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "truncated.jsonl"
    write_rows(
        transcript,
        [{"message": {"role": "assistant", "content": [{"type": "text", "text": "FINAL ANSWER: 7"}]}}],
        truncated=True,
    )
    index = TranscriptIndex.from_path(transcript)

    assert index.parse_failures[0].line_number == 2
    assert index.to_meta()["parse_failures"][0]["line_sha256"]
    assert extract_latest_complete_answer_from_transcript_for_eval(
        transcript,
        transcript_index=index,
    ) == "FINAL ANSWER: 7"
    with pytest.raises(TranscriptIndexError, match="line 2"):
        index.strict_payloads()


def test_index_projection_does_not_modify_original_payload_or_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "projection.jsonl"
    write_rows(transcript, [{"path": "/benchmark/workspace/output.txt"}])
    original = transcript.read_text(encoding="utf-8")
    index = TranscriptIndex.from_path(transcript)

    projected = index.strict_payloads(
        mappings={"/benchmark/workspace": "/host/workspace"},
        projector=lambda value, mappings: {
            "path": value["path"].replace(*next(iter(mappings.items())))
        },
    )

    assert projected == [(1, {"path": "/host/workspace/output.txt"})]
    assert index.entries[0].payload == {"path": "/benchmark/workspace/output.txt"}
    assert transcript.read_text(encoding="utf-8") == original
