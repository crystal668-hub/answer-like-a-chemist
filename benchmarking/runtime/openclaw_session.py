"""Identity-first OpenClaw 9.5 session adapter.

Active benchmark code uses this module instead of inspecting OpenClaw's private
SQLite tables or live transcript files. Legacy JSONL inspection remains in the
historical reader modules.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


class OpenClawSessionError(RuntimeError):
    def __init__(self, message: str, *, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class SessionTarget:
    agent_id: str
    session_key: str
    session_id: str
    state_dir: Path
    config_path: Path


@dataclass(frozen=True)
class SessionEvidence:
    source: str
    row_identity: dict[str, Any] = field(default_factory=dict)
    transcript_identity: dict[str, Any] = field(default_factory=dict)
    trajectory_events_path: str = ""
    transcript_branch_path: str = ""
    export_manifest_path: str = ""
    lifecycle_generation: str = ""
    error: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "row_identity": dict(self.row_identity),
            "transcript_identity": dict(self.transcript_identity),
            "trajectory_events_path": self.trajectory_events_path,
            "transcript_branch_path": self.transcript_branch_path,
            "export_manifest_path": self.export_manifest_path,
            "lifecycle_generation": self.lifecycle_generation,
            "error": dict(self.error),
        }


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return result[:96] or "session"


def make_session_target(*, agent_id: str, session_id: str, state_dir: Path, config_path: Path) -> SessionTarget:
    agent = _slug(agent_id).lower()
    sid = _slug(session_id)
    return SessionTarget(agent, f"agent:{agent}:explicit:{sid}", sid, Path(state_dir).resolve(), Path(config_path).resolve())


def _default_runner(command: list[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)
    return subprocess.run(command, cwd=str(cwd) if cwd else None, env=merged_env, capture_output=True, text=True, check=False)


class OpenClawSessionAdapter:
    def __init__(self, *, executable: str | None = None, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None):
        self.executable = executable or shutil.which("openclaw") or "openclaw"
        self._runner = runner or _default_runner

    def _run(self, args: list[str], *, target: SessionTarget) -> subprocess.CompletedProcess[str]:
        env = {"OPENCLAW_STATE_DIR": str(target.state_dir)}
        try:
            return self._runner([self.executable, *args], cwd=target.state_dir, env=env)
        except TypeError:
            return self._runner([self.executable, *args], cwd=target.state_dir)
        except OSError as exc:
            raise OpenClawSessionError(str(exc), code="openclaw_unavailable") from exc

    @staticmethod
    def _json(stdout: str, *, code: str) -> Any:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise OpenClawSessionError("OpenClaw returned invalid JSON", code=code, details={"stdout": stdout[-1000:]}) from exc

    def inspect(self, target: SessionTarget) -> SessionEvidence:
        result = self._run(["sessions", "--json", "--agent", target.agent_id], target=target)
        if result.returncode != 0:
            raise OpenClawSessionError("OpenClaw session inventory failed", code="session_inventory_failed", details={"stderr": result.stderr[-1000:]})
        payload = self._json(result.stdout, code="session_inventory_invalid")
        rows = payload.get("sessions", payload) if isinstance(payload, dict) else payload
        rows = rows if isinstance(rows, list) else []
        row = next((item for item in rows if isinstance(item, dict) and (item.get("sessionKey") == target.session_key or item.get("sessionId") == target.session_id)), None)
        if row is None:
            raise OpenClawSessionError("Requested OpenClaw session row was not found", code="session_row_missing", details={"session_key": target.session_key, "session_id": target.session_id})
        if str(row.get("agentId") or target.agent_id) != target.agent_id:
            raise OpenClawSessionError("OpenClaw session owner does not match target agent", code="session_owner_mismatch", details={"row": row})
        return SessionEvidence(source="sqlite", row_identity=dict(row), transcript_identity={"sessionKey": target.session_key, "sessionId": target.session_id}, lifecycle_generation=str(row.get("generation") or row.get("updatedAt") or ""))

    def export_trajectory(self, target: SessionTarget, *, output_dir: Path) -> SessionEvidence:
        output_dir.mkdir(parents=True, exist_ok=True)
        result = self._run(["sessions", "export-trajectory", "--session-key", target.session_key, "--workspace", str(output_dir), "--json"], target=target)
        if result.returncode != 0:
            raise OpenClawSessionError("OpenClaw trajectory export failed", code="trajectory_export_failed", details={"stderr": result.stderr[-1000:]})
        payload = self._json(result.stdout, code="trajectory_export_invalid")
        data = payload.get("export", payload) if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}
        events = str(data.get("eventsPath") or data.get("trajectoryEventsPath") or output_dir / "events.jsonl")
        branch = str(data.get("sessionBranchPath") or data.get("transcriptBranchPath") or output_dir / "session-branch.json")
        manifest = str(data.get("manifestPath") or output_dir / "artifacts.json")
        return SessionEvidence(source="trajectory_export", row_identity={"sessionKey": target.session_key, "sessionId": target.session_id, "agentId": target.agent_id}, transcript_identity={"sessionKey": target.session_key, "sessionId": target.session_id}, trajectory_events_path=events, transcript_branch_path=branch, export_manifest_path=manifest, lifecycle_generation=str(data.get("generation") or ""))

    def collect(self, target: SessionTarget, *, output_dir: Path) -> SessionEvidence:
        try:
            row = self.inspect(target)
        except OpenClawSessionError as exc:
            if exc.code in {"session_owner_mismatch", "session_lock_conflict"}:
                raise
            row = SessionEvidence(source="sqlite", error={"code": exc.code, "message": str(exc), "details": exc.details})
        try:
            exported = self.export_trajectory(target, output_dir=output_dir)
        except OpenClawSessionError as exc:
            return SessionEvidence(source=row.source, row_identity=row.row_identity, transcript_identity=row.transcript_identity, lifecycle_generation=row.lifecycle_generation, error={"code": exc.code, "message": str(exc), "details": exc.details})
        return SessionEvidence(source=exported.source, row_identity=row.row_identity or exported.row_identity, transcript_identity=exported.transcript_identity, trajectory_events_path=exported.trajectory_events_path, transcript_branch_path=exported.transcript_branch_path, export_manifest_path=exported.export_manifest_path, lifecycle_generation=exported.lifecycle_generation or row.lifecycle_generation, error=row.error)
