from pathlib import Path
from types import SimpleNamespace

from benchmarking.core.attempt_outcome import (
    AttemptAnswerSource,
    AttemptEvidence,
    AttemptRetryDecision,
    AttemptTerminalStatus,
    determine_attempt_outcome,
)
from benchmarking.core.contracts import RunStatus
from benchmarking.core.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript_for_eval,
    summarize_transcript_convergence,
)
from benchmarking.service.single.runner import SingleLLMRunner

FIXTURES = Path(__file__).parent / "fixtures" / "single_llm"


def test_complete_answer_wins_over_historical_idle_timeout() -> None:
    transcript = FIXTURES / "historical_timeout_then_answer.jsonl"
    convergence = summarize_transcript_convergence(transcript)
    answer = extract_latest_complete_answer_from_transcript_for_eval(transcript, eval_kind="verifier_grounded")

    outcome = determine_attempt_outcome(
        AttemptEvidence(
            native_result_status="completed",
            native_payload_complete=True,
            transcript_complete_answer=bool(answer),
            finalization_rescue_complete=False,
            current_process_exit=0,
            current_failure_code="",
            current_failure_retryable=False,
            historical_prompt_errors=tuple(convergence["historical_prompt_errors"]),
        )
    )

    assert outcome.terminal_status is AttemptTerminalStatus.COMPLETED
    assert outcome.answer_source is AttemptAnswerSource.NATIVE
    assert outcome.retry_decision is AttemptRetryDecision.NO_RETRY
    assert outcome.historical_prompt_errors == ("LLM idle timeout (120s): no response from model",)


def test_failed_current_idle_timeout_without_answer_is_retryable() -> None:
    outcome = determine_attempt_outcome(
        AttemptEvidence(
            native_result_status="failed",
            native_payload_complete=False,
            transcript_complete_answer=False,
            finalization_rescue_complete=False,
            current_process_exit=0,
            current_failure_code="openclaw_idle_watchdog",
            current_failure_retryable=True,
        )
    )
    assert outcome.terminal_status is AttemptTerminalStatus.FAILED
    assert outcome.answer_source is AttemptAnswerSource.NONE
    assert outcome.retry_decision is AttemptRetryDecision.RETRY
    assert outcome.retry_reason == "openclaw_idle_watchdog"


def test_recovered_answer_wins_over_current_session_error() -> None:
    outcome = determine_attempt_outcome(
        AttemptEvidence(
            native_result_status="recovered",
            native_payload_complete=False,
            transcript_complete_answer=True,
            finalization_rescue_complete=False,
            current_process_exit=1,
            current_failure_code="openclaw_session_takeover",
            current_failure_retryable=False,
        )
    )
    assert outcome.terminal_status is AttemptTerminalStatus.RECOVERED
    assert outcome.answer_source is AttemptAnswerSource.TRANSCRIPT
    assert outcome.retry_decision is AttemptRetryDecision.NO_RETRY


def test_zero_container_exit_with_timeout_payload_uses_payload_outcome() -> None:
    runner = object.__new__(SingleLLMRunner)
    runner.convergence_policy = ConvergencePolicy(timeout_seconds=900)
    runner.no_timeout = False
    runner.configured_skills = ()
    runner._summarize_payloads = lambda payloads: "\n\n".join(str(item.get("text") or "") for item in payloads)
    runner._normalize_answer_tracks = lambda *, full_response_text: ("", full_response_text)
    result_payload = {
        "payloads": [{"text": "LLM request timed out."}],
        "meta": {
            "aborted": True,
            "livenessState": "blocked",
            "stdout_diagnostics": {"schema_valid": True},
            "session_isolation": {"session_isolation_ok": True},
        },
    }

    result = runner._build_container_runner_result(
        payload={"result": result_payload},
        result_payload=result_payload,
        runner_meta=dict(result_payload["meta"]),
        record=SimpleNamespace(eval_kind="verifier_grounded", payload={}, grading=None),
        group=SimpleNamespace(skills_enabled=True),
        input_bundle=None,
        session_id="session-1",
    )

    assert result.status is RunStatus.FAILED
    assert result.failure is not None
    assert result.failure.code == "agent_response_timeout"
    assert runner._timeout_retry_decision(result).retryable is True


def test_container_lifecycle_paths_follow_workspace_archive() -> None:
    active = Path("/host/active")
    archive = Path("/host/archive")
    translated = SingleLLMRunner._translate_container_paths(
        {
            "session_path": "/benchmark/session/agents/agent/sessions/session.jsonl",
            "snapshot_path": "/benchmark/workspace/scratch/notes/session-snapshots/session.jsonl",
        },
        session_root=active / "scratch/session",
        workspace=active,
    )
    archived = SingleLLMRunner._translate_path_prefix(translated, source=active, target=archive)

    assert archived == {
        "session_path": "/host/archive/scratch/session/agents/agent/sessions/session.jsonl",
        "snapshot_path": "/host/archive/scratch/notes/session-snapshots/session.jsonl",
    }
