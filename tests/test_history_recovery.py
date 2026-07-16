from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmarking.runtime.history_recovery import replay_workspace_adjudication


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_history_replay_is_dry_run_first_and_apply_snapshots_atomically(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    runtime_root = tmp_path / "runtime"
    active_workspace = runtime_root / "run" / "invocation" / "active" / "group" / "agent"
    typo_target = runtime_root / "run" / "invocation" / "sibling-typo" / "generated.py"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call-write",
                            "name": "write",
                            "arguments": {"path": str(typo_target), "content": "print('owned')\n"},
                        }
                    ],
                }
            }
        )
        + "\n"
        + json.dumps(
            {
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-write",
                    "toolName": "write",
                    "content": [{"type": "text", "text": "Wrote file"}],
                    "isError": False,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    record = {
        "schema_version": 2,
        "group_id": "single_llm_skills_on",
        "group_label": "skills on",
        "runner": "single_llm",
        "websearch": False,
        "skills_enabled": True,
        "record_id": "record_one",
        "subset": "demo",
        "dataset": "demo",
        "source_file": str(tmp_path / "demo.jsonl"),
        "eval_kind": "verifier_grounded",
        "prompt": "Q",
        "reference_answer": "",
        "answer_text": "FINAL ANSWER: X",
        "short_answer_text": "X",
        "full_response_text": "FINAL ANSWER: X",
        "evaluation": {"score": 0.0, "normalized_score": 0.0, "passed": None, "details": {}},
        "runner_meta": {
            "session_isolation": {"postflight_entry_session_file": str(transcript)},
            "workspace_isolation": {"active_workspace": str(active_workspace), "archive_ok": True},
            "workspace_scratch": {"scratch_dir": str(active_workspace / ".benchmark-scratch" / "record")},
        },
        "raw": {},
        "elapsed_seconds": 1.0,
        "run_lifecycle_status": "failed",
        "protocol_completion_status": "missing",
        "protocol_acceptance_status": None,
        "answer_availability": "native_final",
        "answer_reliability": "native",
        "evaluable": False,
        "scored": False,
        "recovery_mode": "none",
        "degraded_execution": True,
        "execution_error_kind": "execution_error",
        "error": "legacy workspace failure",
    }
    record_path = run_root / "per-record" / "single_llm_skills_on" / "record-one.json"
    write_json(record_path, record)
    write_json(run_root / "results.json", {"schema_version": 2, "results": [record], "summary": {}})
    write_json(run_root / "progress" / "state.json", {"status": "completed"})
    write_json(
        run_root / "runtime-manifest.json",
        {
            "workspace_isolation": {
                "run_id": "run",
                "invocation_id": "invocation",
                "runtime_runs_root": str(runtime_root),
                "forbidden_path_policy": {
                    "protected_roots": [
                        {
                            "policy_id": "benchmark_runtime_root",
                            "path": str(runtime_root),
                            "source": "test.runtime",
                        }
                    ]
                },
            }
        },
    )
    original = record_path.read_bytes()

    dry_run = replay_workspace_adjudication(
        run_root=run_root,
        group_id="single_llm_skills_on",
        record_ids=["record_one"],
    )

    assert dry_run["mode"] == "dry_run"
    assert dry_run["model_calls"] == 0
    assert dry_run["records"][0]["audit"]["adjudication"] == "scoreable_degraded"
    assert dry_run["records"][0]["audit"]["finding_count"] == 1
    assert dry_run["records"][0]["historical_review"]["write_only"] is True
    assert record_path.read_bytes() == original

    applied = replay_workspace_adjudication(
        run_root=run_root,
        group_id="single_llm_skills_on",
        record_ids=["record_one"],
        apply=True,
        rescore=True,
        approve_historical_ownership=True,
        evaluator=lambda _payload: {
            "eval_kind": "verifier_grounded",
            "score": 0.75,
            "max_score": 1.0,
            "normalized_score": 0.75,
            "passed": None,
            "primary_metric": "verifier_score",
            "primary_metric_direction": "higher_is_better",
            "details": {"method": "test"},
        },
    )

    updated = json.loads(record_path.read_text(encoding="utf-8"))
    assert applied["mode"] == "apply"
    assert Path(applied["snapshot"]).is_dir()
    assert updated["schema_version"] == 3
    assert updated["scored"] is True
    assert updated["evaluation"]["score"] == 0.75
    assert updated["runner_meta"]["workspace_isolation"]["adjudication"] == "scoreable_degraded"
    assert json.loads((run_root / "results.json").read_text(encoding="utf-8"))["schema_version"] == 3
    progress = json.loads((run_root / "progress" / "state.json").read_text(encoding="utf-8"))
    assert progress["workspace_adjudication_recovery"]["model_calls"] == 0


def test_history_replay_script_is_directly_executable() -> None:
    project_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "replay_workspace_adjudication.py"), "--help"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Replay benchmark workspace audit" in completed.stdout
