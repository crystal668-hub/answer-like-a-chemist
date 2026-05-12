from __future__ import annotations

import json
import subprocess
from pathlib import Path

from benchmarking import automated_evaluation
from benchmarking import automated_evaluation_launcher


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n", encoding="utf-8")


def make_executable(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def minimal_record_payload(*, group_id: str, record_id: str, runner: str, runner_meta: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "group_id": group_id,
        "group_label": group_id,
        "runner": runner,
        "websearch": True,
        "skills_enabled": group_id.endswith("skills_on"),
        "record_id": record_id,
        "subset": "chembench",
        "dataset": "demo",
        "source_file": "/tmp/demo.jsonl",
        "eval_kind": "chembench_open_ended",
        "prompt": "What is the answer?",
        "reference_answer": "A",
        "answer_text": "FINAL ANSWER: A",
        "short_answer_text": "A",
        "full_response_text": "FINAL ANSWER: A",
        "evaluation": {
            "eval_kind": "chembench_open_ended",
            "score": 1.0,
            "max_score": 1.0,
            "normalized_score": 1.0,
            "passed": True,
            "primary_metric": "exact",
            "primary_metric_direction": "higher_is_better",
            "details": {"judge": {"summary": "matches"}},
        },
        "runner_meta": runner_meta,
        "raw": {},
        "elapsed_seconds": 1.5,
        "run_lifecycle_status": "completed",
        "protocol_completion_status": "completed",
        "protocol_acceptance_status": None,
        "answer_availability": "native_final",
        "answer_reliability": "native",
        "evaluable": True,
        "scored": True,
        "recovery_mode": "none",
        "degraded_execution": False,
        "execution_error_kind": None,
        "error": None,
    }


def write_minimal_run(output_root: Path) -> None:
    result = minimal_record_payload(group_id="single_llm_skills_off", record_id="r1", runner="single_llm", runner_meta={})
    write_json(
        output_root / "results.json",
        {
            "schema_version": 2,
            "generated_at": "2026-05-11T00:00:00+0000",
            "groups": [{"id": "single_llm_skills_off"}],
            "results": [result],
            "summary": {"groups": {"single_llm_skills_off": {"count": 1}}},
        },
    )
    write_json(output_root / "runtime-manifest.json", {"run_groups": ["single_llm_skills_off"]})


def test_single_llm_transcript_summary_skips_thinking_and_keeps_visible_evidence(tmp_path: Path) -> None:
    transcript_path = tmp_path / "session.jsonl"
    write_jsonl(
        transcript_path,
        [
            {
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Benchmark question text"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "SECRET INTERNAL REASONING"},
                        {"type": "text", "text": "I will inspect a skill."},
                        {"type": "toolCall", "id": "call-1", "name": "read", "input": {"path": "SKILL.md"}},
                    ],
                }
            },
            {
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "toolName": "read",
                    "content": [{"type": "text", "text": "Skill file body"}],
                }
            },
            {
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "FINAL ANSWER: A"}],
                }
            },
        ],
    )

    summary = automated_evaluation.summarize_single_llm_transcript(transcript_path, max_text_chars=200)

    serialized = json.dumps(summary, ensure_ascii=False)
    assert "SECRET INTERNAL REASONING" not in serialized
    assert summary["exists"] is True
    assert summary["user_message_count"] == 1
    assert summary["assistant_message_count"] == 2
    assert summary["tool_calls"][0]["name"] == "read"
    assert summary["tool_results"][0]["tool_name"] == "read"
    assert summary["assistant_text_tail"][-1]["text"] == "FINAL ANSWER: A"


def test_build_input_bundle_groups_records_and_warns_on_missing_transcript(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    missing_transcript = output_root / "missing-session.jsonl"
    result = minimal_record_payload(
        group_id="single_llm_skills_on",
        record_id="r1",
        runner="single_llm",
        runner_meta={
            "session_isolation": {"postflight_entry_session_file": str(missing_transcript)},
            "skill_use_audit": {"tool_call_count": 0, "no_tool_call": True},
        },
    )
    write_json(
        output_root / "results.json",
        {
            "schema_version": 2,
            "generated_at": "2026-05-11T00:00:00+0000",
            "results": [result],
            "summary": {"groups": {"single_llm_skills_on": {"count": 1}}},
        },
    )
    write_json(output_root / "runtime-manifest.json", {"run_groups": ["single_llm_skills_on"]})

    bundle = automated_evaluation.build_input_bundle(output_root)

    assert bundle["schema_version"] == 1
    assert bundle["run_summary"]["record_count"] == 1
    assert bundle["records"][0]["record_id"] == "r1"
    assert bundle["records"][0]["groups"][0]["group_id"] == "single_llm_skills_on"
    assert bundle["records"][0]["groups"][0]["trajectory"]["kind"] == "single_llm_transcript"
    assert any("missing transcript" in warning["message"] for warning in bundle["warnings"])


def test_chemqa_artifact_summary_reads_archive_files(tmp_path: Path) -> None:
    archive_dir = tmp_path / "artifacts" / "chemqa_skills_on" / "r1" / "run-1"
    write_json(archive_dir / "artifact_manifest.json", {"artifacts": [{"name": "final_answer_artifact"}]})
    write_json(archive_dir / "candidate_view.json", {"direct_answer": "A"})
    write_json(archive_dir / "proposer_trajectory.json", {"steps": ["draft"]})
    write_json(archive_dir / "qa_result.json", {"terminal_state": "completed", "final_answer": "A"})

    summary = automated_evaluation.summarize_chemqa_artifacts(
        {"archive_dir": str(archive_dir), "qa_result_path": str(archive_dir / "qa_result.json")},
        max_text_chars=500,
    )

    assert summary["kind"] == "chemqa_artifacts"
    assert summary["archive_dir"] == str(archive_dir)
    assert summary["files"]["artifact_manifest"]["exists"] is True
    assert summary["files"]["candidate_view"]["preview"]["direct_answer"] == "A"
    assert summary["files"]["proposer_trajectory"]["exists"] is True


def test_launcher_starts_detached_background_process_and_writes_status(tmp_path: Path) -> None:
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 12345

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        calls.append({"command": command, **kwargs})
        return FakeProcess()

    status = automated_evaluation_launcher.launch_automated_evaluation(
        tmp_path / "run",
        popen=fake_popen,
        python_executable="/venv/bin/python",
    )

    assert status["status"] == "launched"
    assert status["pid"] == 12345
    assert calls[0]["command"][:4] == ["/venv/bin/python", "-m", "benchmarking.automated_evaluation", "run"]
    assert "--output-root" in calls[0]["command"]
    assert calls[0]["start_new_session"] is True
    status_payload = json.loads((tmp_path / "run" / "analysis" / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "launched"
    assert status_payload["pid"] == 12345


def test_launcher_records_launch_failure_without_raising(tmp_path: Path) -> None:
    def fake_popen(command: list[str], **kwargs: object) -> object:
        raise OSError("codex runner unavailable")

    status = automated_evaluation_launcher.launch_automated_evaluation(
        tmp_path / "run",
        popen=fake_popen,
        python_executable="/venv/bin/python",
    )

    assert status["status"] == "launch_failed"
    assert "codex runner unavailable" in status["error"]
    status_payload = json.loads((tmp_path / "run" / "analysis" / "status.json").read_text(encoding="utf-8"))
    assert status_payload["status"] == "launch_failed"


def test_resolve_codex_binary_prefers_explicit_executable(tmp_path: Path) -> None:
    explicit = make_executable(tmp_path / "explicit" / "codex")
    path_candidate = make_executable(tmp_path / "path" / "codex")
    app_bundle_candidate = make_executable(tmp_path / "Codex.app" / "Contents" / "Resources" / "codex")

    resolved, candidates = automated_evaluation.resolve_codex_binary(
        explicit,
        which_func=lambda _name: path_candidate,
        app_bundle_bin=app_bundle_candidate,
    )

    assert resolved == str(Path(explicit).resolve())
    assert candidates[0]["source"] == "explicit"
    assert candidates[0]["usable"] is True


def test_resolve_codex_binary_uses_app_bundle_when_path_missing(tmp_path: Path) -> None:
    app_bundle_candidate = make_executable(tmp_path / "Codex.app" / "Contents" / "Resources" / "codex")

    resolved, candidates = automated_evaluation.resolve_codex_binary(
        None,
        which_func=lambda _name: None,
        app_bundle_bin=app_bundle_candidate,
    )

    assert resolved == str(Path(app_bundle_candidate).resolve())
    assert candidates[-1]["source"] == "app_bundle"
    assert candidates[-1]["usable"] is True


def test_run_automated_evaluation_falls_back_when_codex_binary_unavailable(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    write_minimal_run(output_root)

    def fail_if_called(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"unexpected subprocess call: {command}")

    status = automated_evaluation.run_automated_evaluation(
        output_root,
        run_subprocess=fail_if_called,
        codex_which=lambda _name: None,
        app_bundle_bin=tmp_path / "missing-codex",
    )

    assert status["status"] == "failed"
    assert status["stage"] == "codex_resolve"
    report = json.loads((output_root / "analysis" / "report.json").read_text(encoding="utf-8"))
    assert report["run_summary"]["analysis_status"] == "fallback"
    assert "No executable Codex binary" in report["run_summary"]["reason"]
    assert (output_root / "analysis" / "report.md").is_file()


def test_run_automated_evaluation_runs_preflight_before_report_with_model_config(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    write_minimal_run(output_root)
    codex_bin = make_executable(tmp_path / "codex")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        last_message_path = Path(command[command.index("--output-last-message") + 1])
        if command[-1] == "hello":
            last_message_path.write_text("hello\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout='{"event":"preflight"}\n', stderr="")
        last_message_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_summary": {"record_count": 1},
                    "per_record_analysis": [{"record_id": "r1"}],
                    "cross_record_patterns": [],
                    "architecture_recommendations": [],
                    "skill_orchestration_recommendations": [],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout='{"event":"report"}\n', stderr="")

    status = automated_evaluation.run_automated_evaluation(
        output_root,
        codex_bin=codex_bin,
        run_subprocess=fake_run,
    )

    assert status["status"] == "completed"
    assert len(calls) == 2
    assert calls[0][-1] == "hello"
    for command in calls:
        assert command[0] == str(Path(codex_bin).resolve())
        assert command[command.index("--model") + 1] == "gpt-5.5"
        assert command[command.index("-c") + 1] == 'model_reasoning_effort="xhigh"'
    assert "codex-preflight-last-message.txt" in calls[0][calls[0].index("--output-last-message") + 1]
    assert "codex-last-message.txt" in calls[1][calls[1].index("--output-last-message") + 1]


def test_run_automated_evaluation_skips_report_when_preflight_fails(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    write_minimal_run(output_root)
    codex_bin = make_executable(tmp_path / "codex")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 42, stdout='{"event":"preflight_failed"}\n', stderr="not available")

    status = automated_evaluation.run_automated_evaluation(
        output_root,
        codex_bin=codex_bin,
        run_subprocess=fake_run,
    )

    assert status["status"] == "failed"
    assert status["stage"] == "codex_preflight"
    assert len(calls) == 1
    assert calls[0][-1] == "hello"
    report = json.loads((output_root / "analysis" / "report.json").read_text(encoding="utf-8"))
    assert report["run_summary"]["analysis_status"] == "fallback"
    assert "Codex preflight failed" in report["run_summary"]["reason"]
    assert (output_root / "analysis" / "report.md").is_file()


def test_run_automated_evaluation_writes_report_from_fake_codex(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    write_minimal_run(output_root)
    codex_bin = make_executable(tmp_path / "codex")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        last_message_path = Path(command[command.index("--output-last-message") + 1])
        last_message_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_summary": {"record_count": 1},
                    "per_record_analysis": [
                        {
                            "record_id": "r1",
                            "standard_answer_delta": "matches",
                            "trajectory_delta": "no tools",
                            "recommendations": ["keep baseline"],
                        }
                    ],
                    "cross_record_patterns": ["single record"],
                    "architecture_recommendations": ["add more evidence"],
                    "skill_orchestration_recommendations": ["route calculators"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout='{"event":"done"}\n', stderr="")

    status = automated_evaluation.run_automated_evaluation(
        output_root,
        codex_bin=codex_bin,
        run_subprocess=fake_run,
    )

    assert status["status"] == "completed"
    report = json.loads((output_root / "analysis" / "report.json").read_text(encoding="utf-8"))
    assert report["per_record_analysis"][0]["record_id"] == "r1"
    assert "Automated Benchmark Evaluation" in (output_root / "analysis" / "report.md").read_text(encoding="utf-8")
    assert (output_root / "analysis" / "input-bundle.json").is_file()
    assert (output_root / "analysis" / "codex-events.jsonl").read_text(encoding="utf-8") == '{"event":"done"}\n'


def test_run_automated_evaluation_falls_back_when_codex_report_is_invalid(tmp_path: Path) -> None:
    output_root = tmp_path / "run"
    write_minimal_run(output_root)
    codex_bin = make_executable(tmp_path / "codex")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        last_message_path = Path(command[command.index("--output-last-message") + 1])
        last_message_path.write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    status = automated_evaluation.run_automated_evaluation(
        output_root,
        codex_bin=codex_bin,
        run_subprocess=fake_run,
    )

    assert status["status"] == "failed"
    assert status["stage"] == "report_validation"
    report = json.loads((output_root / "analysis" / "report.json").read_text(encoding="utf-8"))
    assert report["run_summary"]["analysis_status"] == "fallback"
    assert report["per_record_analysis"][0]["record_id"] == "r1"
