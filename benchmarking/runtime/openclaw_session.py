"""OpenClaw 9.5 public CLI session inventory and immutable export adapter.

The CLI owns SQLite access. Only exported bundles are read by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from benchmarking.runtime.transcript_index import TranscriptIndex


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
    transcript_path: str = ""
    lifecycle_generation: str = ""
    fingerprints: dict[str, str] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_session_target(*, agent_id: str, session_id: str, state_dir: Path, config_path: Path) -> SessionTarget:
    # Never truncate or rewrite session IDs: retries must not alias each other.
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", agent_id):
        raise ValueError("Invalid OpenClaw agent id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", session_id):
        raise ValueError("Invalid OpenClaw session id")
    return SessionTarget(agent_id, f"agent:{agent_id}:explicit:{session_id}", session_id,
                         Path(state_dir).resolve(), Path(config_path).resolve())


def _default_runner(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, **kwargs)


def _failure(result: subprocess.CompletedProcess[str], fallback: str) -> OpenClawSessionError:
    text = (str(result.stderr or "") + "\n" + str(result.stdout or "")).lower()
    code = fallback
    if "agent-embedded" in text and any(word in text for word in ("lock", "writer", "held")):
        code = "session_lock_conflict"
    elif any(word in text for word in ("restore-required", "restore required", "cold transcript", "cold storage")):
        code = "cold_transcript_unavailable"
    elif "owner" in text and any(word in text for word in ("mismatch", "conflict")):
        code = "session_owner_mismatch"
    return OpenClawSessionError("OpenClaw session command failed", code=code,
                               details={"returncode": result.returncode})


class OpenClawSessionAdapter:
    def __init__(self, *, executable: str | None = None, runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
                 environment: Mapping[str, str] | None = None):
        self.executable = executable or shutil.which("openclaw") or "openclaw"
        self._runner = runner or _default_runner
        self.environment = dict(os.environ if environment is None else environment)

    def _run(self, args: list[str], *, target: SessionTarget) -> subprocess.CompletedProcess[str]:
        env = {**self.environment, "OPENCLAW_STATE_DIR": str(target.state_dir),
               "OPENCLAW_CONFIG_PATH": str(target.config_path)}
        try:
            return self._runner([self.executable, *args], cwd=target.state_dir, env=env, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise OpenClawSessionError("Session evidence command timed out", code="session_evidence_timeout") from exc
        except OSError as exc:
            raise OpenClawSessionError("OpenClaw evidence command unavailable", code="openclaw_unavailable") from exc

    @staticmethod
    def _json(text: str, *, code: str) -> dict[str, Any]:
        try:
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("Expected object")
            return value
        except ValueError as exc:
            raise OpenClawSessionError("Invalid OpenClaw JSON object", code=code) from exc

    def inspect(self, target: SessionTarget) -> SessionEvidence:
        result = self._run(["sessions", "--json", "--agent", target.agent_id, "--limit", "all"], target=target)
        if result.returncode != 0:
            raise _failure(result, "session_inventory_failed")
        payload = self._json(result.stdout, code="session_inventory_invalid")
        rows = payload.get("sessions")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise OpenClawSessionError("Invalid session inventory", code="session_inventory_invalid")
        matches = [row for row in rows if row.get("key") == target.session_key]
        if not matches:
            code = "session_key_mismatch" if any(row.get("sessionId") == target.session_id for row in rows) else "session_row_missing"
            raise OpenClawSessionError("Requested session key not found", code=code)
        if len(matches) != 1:
            raise OpenClawSessionError("Ambiguous session owner", code="session_owner_mismatch")
        row = matches[0]
        if row.get("agentId") != target.agent_id:
            raise OpenClawSessionError("Session owner mismatch", code="session_owner_mismatch")
        if row.get("sessionId") != target.session_id:
            raise OpenClawSessionError("Session ID mismatch", code="session_id_mismatch")
        identity = {**row, "store_identity": payload.get("path"), "store_kind": "sqlite"}
        # updatedAt is a timestamp, never a generation cursor.
        return SessionEvidence(source="sqlite", row_identity=identity,
                               transcript_identity={"sessionKey": target.session_key, "sessionId": target.session_id})

    def export_trajectory(self, target: SessionTarget, *, output_dir: Path) -> SessionEvidence:
        output_dir.mkdir(parents=True, exist_ok=True)
        name = uuid.uuid4().hex
        result = self._run(["sessions", "export-trajectory", "--agent", target.agent_id,
                            "--session-key", target.session_key, "--workspace", str(output_dir),
                            "--output", name, "--json"], target=target)
        if result.returncode != 0:
            raise _failure(result, "trajectory_export_failed")
        payload = self._json(result.stdout, code="trajectory_export_invalid")
        raw_root = payload.get("outputDir")
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise OpenClawSessionError("Export did not report an output directory", code="trajectory_export_invalid")
        root = Path(raw_root).expanduser().resolve()
        base = (output_dir / ".openclaw" / "trajectory-exports").resolve()
        if root == base or base not in root.parents:
            raise OpenClawSessionError("Unexpected export directory", code="trajectory_export_invalid")
        if payload.get("sessionId") != target.session_id:
            raise OpenClawSessionError("Export session ID mismatch", code="session_id_mismatch")
        paths = {name: root / name for name in ("events.jsonl", "session-branch.json", "manifest.json")}
        for path in paths.values():
            if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.is_file():
                raise OpenClawSessionError("Missing or unsafe trajectory artifact", code="trajectory_export_missing")
        try:
            manifest = self._json(paths["manifest.json"].read_text(), code="trajectory_export_invalid")
            if manifest.get("sessionId") != target.session_id or manifest.get("sessionKey") != target.session_key:
                raise OpenClawSessionError("Export manifest identity mismatch", code="session_key_mismatch")
            branch = self._json(paths["session-branch.json"].read_text(), code="trajectory_export_invalid")
            entries = branch.get("entries")
            if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
                raise OpenClawSessionError("Invalid transcript branch", code="trajectory_export_invalid")
            # Project the exported native branch, not trajectory envelopes, for
            # existing answer/tool/audit consumers. Retain exporter warnings as
            # parse failures so audit cannot silently treat partial data as clean.
            trajectory = TranscriptIndex.from_path(paths["events.jsonl"])
            degraded = bool(manifest.get("warnings") or trajectory.parse_failures)
            transcript = root / "transcript.jsonl"
            with transcript.open("w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if degraded:
                    handle.write("[incomplete OpenClaw export]\n")
            paths["transcript.jsonl"] = transcript
            return SessionEvidence(source="trajectory_export",
                transcript_identity={"sessionKey": target.session_key, "sessionId": target.session_id, "leafId": branch.get("leafId")},
                trajectory_events_path=str(paths["events.jsonl"]), transcript_branch_path=str(paths["session-branch.json"]),
                export_manifest_path=str(paths["manifest.json"]), transcript_path=str(transcript),
                fingerprints={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
                error={"code": "trajectory_export_partial"} if degraded else {})
        except (OSError, UnicodeError) as exc:
            raise OpenClawSessionError("Unreadable trajectory export", code="trajectory_export_invalid") from exc

    def collect(self, target: SessionTarget, *, output_dir: Path) -> SessionEvidence:
        try:
            row = self.inspect(target)
        except OpenClawSessionError as exc:
            return SessionEvidence(source="sqlite", error={"code": exc.code, "message": str(exc), "details": exc.details})
        try:
            exported = self.export_trajectory(target, output_dir=output_dir)
        except OpenClawSessionError as exc:
            return replace(row, error={"code": exc.code, "message": str(exc), "details": exc.details})
        return replace(exported, row_identity=row.row_identity)
