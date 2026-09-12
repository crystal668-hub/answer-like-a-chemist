import json
from pathlib import Path

import pytest

from benchmarking.runtime.error_capture import capture_execution_error
from benchmarking.runtime.session_lifecycle import (
    SessionFileFingerprint,
    SessionLifecycleSupervisor,
    SessionOwnershipError,
    provider_observability,
    session_takeover_reasons,
)

FIXTURES = Path(__file__).parent / "fixtures" / "single_llm"


def _config(root: Path) -> Path:
    agent_dir = root / "session" / "agents" / "agent" / "agent"
    agent_dir.mkdir(parents=True)
    path = root / "openclaw.json"
    path.write_text(
        json.dumps({"agents": {"list": [{"id": "agent", "agentDir": str(agent_dir)}]}}),
        encoding="utf-8",
    )
    return path


def test_session_takeover_preserves_original_error_path_lanes_and_terminal_policy() -> None:
    stderr = (FIXTURES / "session_takeover.stderr.txt").read_text(encoding="utf-8")
    classification = capture_execution_error(returncode=1, stdout="", stderr=stderr, session_id="session-1")

    assert classification.code == "openclaw_session_takeover"
    assert classification.layer == "openclaw_session"
    assert classification.retryable is False
    assert classification.details["session_path"] == "/benchmark/session/agents/agent/sessions/session-1.jsonl"
    assert classification.details["lanes"] == ["main", "session:agent:agent:explicit:session-1"]
    assert any("EmbeddedAttemptSessionTakeoverError" in line for line in classification.details["matched_lines"])


def test_session_fence_detects_owner_and_inode_changes() -> None:
    reasons = session_takeover_reasons(
        expected_owner_token="owner-a",
        observed_owner_token="owner-b",
        before=SessionFileFingerprint(True, 1, 10, 20, 30),
        after=SessionFileFingerprint(True, 1, 11, 21, 31),
        lock_released=True,
    )
    assert reasons == ["owner_token_changed", "session_file_replaced"]


def test_supervisor_excludes_second_owner_and_freezes_transcript(tmp_path: Path) -> None:
    config = _config(tmp_path)
    evidence = tmp_path / "workspace" / "scratch" / "notes" / "session-lifecycle.json"
    first = SessionLifecycleSupervisor(
        attempt_id="run/group/record/0",
        agent_id="agent",
        session_id="session-1",
        config_path=config,
        evidence_path=evidence,
    )
    second = SessionLifecycleSupervisor(
        attempt_id="run/group/record/0",
        agent_id="agent",
        session_id="session-1",
        config_path=config,
        evidence_path=evidence.with_name("second.json"),
    )
    with first:
        with pytest.raises(SessionOwnershipError):
            second.__enter__()
        first.invocation_started(kind="primary", session_id="session-1", child_pid=123)
        first.session_path("session-1").write_text('{"message":{"role":"assistant"}}\n', encoding="utf-8")
        first.invocation_finished(session_id="session-1", returncode=0, stdout="", stderr="")
        lifecycle = first.finalize(status="completed")

    snapshot = evidence.parent / "session-snapshots" / "session-1.jsonl"
    assert snapshot.read_text(encoding="utf-8") == '{"message":{"role":"assistant"}}\n'
    assert lifecycle["final_status"] == "completed"
    assert lifecycle["cleanup"]["owner_lock_released"] is True
    assert lifecycle["cleanup"]["owner_lock_retained"] is True
    assert first.lock_path.read_text(encoding="utf-8") == ""
    assert json.loads(evidence.read_text(encoding="utf-8"))["invocations"][0]["openclaw_pid"] == 123


def test_provider_idle_without_model_completion_is_first_token_timeout() -> None:
    evidence = provider_observability(FIXTURES / "provider_idle_timeout.trajectory.jsonl")
    assert evidence["request_started_at"] == "2026-09-13T00:00:00Z"
    assert evidence["first_response_chunk_at"] == ""
    assert evidence["model_event_completed_at"] == "2026-09-13T00:01:59Z"
    assert evidence["timeout_classification"] == "provider_first_token_timeout"
    assert evidence["watchdog_source"] == "openclaw_idle_watchdog"
