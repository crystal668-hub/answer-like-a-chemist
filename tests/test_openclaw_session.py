from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmarking.runtime.openclaw_session import (
    OpenClawSessionAdapter,
    OpenClawSessionError,
    make_session_target,
)


def test_session_target_uses_stable_explicit_identity(tmp_path: Path) -> None:
    target = make_session_target(
        agent_id="chem-agent",
        session_id="retry-1",
        state_dir=tmp_path / "state",
        config_path=tmp_path / "config.json",
    )
    assert target.agent_id == "chem-agent"
    assert target.session_id == "retry-1"
    assert target.session_key == "agent:chem-agent:explicit:retry-1"


def test_collect_requires_matching_agent_owner_and_exports_once(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["sessions", "--json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"path": "/state/agents/agent/agent/openclaw-agent.sqlite", "sessions": [{"agentId": "agent", "key": "agent:agent:explicit:s", "sessionId": "s"}]}),
                "",
            )
        root = tmp_path / "evidence" / ".openclaw" / "trajectory-exports" / "export"
        root.mkdir(parents=True, exist_ok=True)
        (root / "events.jsonl").write_text(json.dumps({"type": "session.ended", "ts": "2026-01-01T00:00:00Z"}) + "\n")
        (root / "session-branch.json").write_text(json.dumps({"leafId": "e1", "entries": [{"id": "e1", "type": "message"}]})
        )
        (root / "manifest.json").write_text(json.dumps({"sessionId": "s", "sessionKey": "agent:agent:explicit:s"}))
        return subprocess.CompletedProcess(command, 0, json.dumps({"outputDir": str(root), "sessionId": "s"}), "")

    target = make_session_target(agent_id="agent", session_id="s", state_dir=tmp_path / "state", config_path=tmp_path / "config.json")
    evidence = OpenClawSessionAdapter(executable="openclaw", runner=runner).collect(target, output_dir=tmp_path / "evidence")
    assert evidence.source == "trajectory_export"
    assert evidence.row_identity["sessionId"] == "s"
    assert calls[0][1:4] == ["sessions", "--json", "--agent"]
    assert calls[1][1:3] == ["sessions", "export-trajectory"]


def test_owner_mismatch_is_typed_and_not_downgraded(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, json.dumps({"sessions": [{"agentId": "other", "key": "agent:other:explicit:s", "sessionId": "s"}]}), "")

    target = make_session_target(agent_id="agent", session_id="s", state_dir=tmp_path, config_path=tmp_path / "config.json")
    with pytest.raises(OpenClawSessionError, match="Requested session key") as error:
        OpenClawSessionAdapter(runner=runner).inspect(target)
    assert error.value.code == "session_key_mismatch"


def test_export_failure_is_preserved_as_evidence(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:3] == ["sessions", "--json"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"sessions": [{"agentId": "agent", "key": "agent:agent:explicit:s", "sessionId": "s"}]}), "")
        return subprocess.CompletedProcess(command, 2, "", "cold transcript")

    target = make_session_target(agent_id="agent", session_id="s", state_dir=tmp_path, config_path=tmp_path / "config.json")
    evidence = OpenClawSessionAdapter(runner=runner).collect(target, output_dir=tmp_path / "evidence")
    assert evidence.source == "sqlite"
    assert evidence.error["code"] == "cold_transcript_unavailable"
