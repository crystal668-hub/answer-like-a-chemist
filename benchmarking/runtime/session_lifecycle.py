from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarking.runtime.session_isolation import (
    atomic_write_json,
    session_store_path_for_agent,
)
from benchmarking.runtime.observability import decode_transcript_json, observe_transcript_text

TAKEOVER_TEXT = "session file changed while embedded prompt lock was released"
HTTP_TIMEOUT_RE = re.compile(r"\bHTTP(?:\s+status)?\s*(?:408|504)\b", re.I)


class SessionOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionFileFingerprint:
    exists: bool
    device: int | None = None
    inode: int | None = None
    size: int | None = None
    mtime_ns: int | None = None

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)


def session_file_fingerprint(path: Path) -> SessionFileFingerprint:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return SessionFileFingerprint(False)
    return SessionFileFingerprint(True, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def session_takeover_reasons(
    *,
    expected_owner_token: str,
    observed_owner_token: str,
    before: SessionFileFingerprint,
    after: SessionFileFingerprint,
    lock_released: bool,
) -> list[str]:
    reasons: list[str] = []
    if observed_owner_token != expected_owner_token:
        reasons.append("owner_token_changed")
    if lock_released and before.exists and after.exists and (before.device, before.inode) != (after.device, after.inode):
        reasons.append("session_file_replaced")
    return reasons


def provider_observability(trajectory_path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if trajectory_path.is_file():
        trajectory_text = trajectory_path.read_text(encoding="utf-8", errors="replace")
        observe_transcript_text(trajectory_text)
        for line in trajectory_text.splitlines():
            try:
                event = decode_transcript_json(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    submitted = next((event for event in events if event.get("type") == "prompt.submitted"), None)
    completed = [event for event in events if event.get("type") == "model.completed"]
    ended = next((event for event in reversed(events) if event.get("type") == "session.ended"), None)
    ended_data = ended.get("data") if isinstance(ended, dict) and isinstance(ended.get("data"), dict) else {}
    prompt_error = str(ended_data.get("promptError") or "")
    idle_timed_out = ended_data.get("idleTimedOut") is True
    timeout_classification = ""
    if HTTP_TIMEOUT_RE.search(prompt_error):
        timeout_classification = "provider_http_timeout"
    elif idle_timed_out and "no response from model" in prompt_error.lower():
        timeout_classification = "provider_first_token_timeout"
    elif "stream_read_error" in prompt_error.lower() or "stream gap" in prompt_error.lower():
        timeout_classification = "provider_stream_gap_timeout"
    elif "idle timeout" in prompt_error.lower():
        timeout_classification = "openclaw_idle_watchdog"
    return {
        "request_started_at": str(submitted.get("ts") or "") if isinstance(submitted, dict) else "",
        "first_response_chunk_at": "",
        "last_response_chunk_at": "",
        "model_event_completed_at": str(completed[-1].get("ts") or "") if completed else "",
        "ended_at": str(ended.get("ts") or "") if isinstance(ended, dict) else "",
        "end_reason": str(ended_data.get("status") or ""),
        "prompt_error": prompt_error,
        "prompt_error_source": "openclaw_session_ended" if prompt_error else "",
        "idle_timed_out": idle_timed_out,
        "timeout_classification": timeout_classification,
        "watchdog_source": "openclaw_idle_watchdog" if idle_timed_out else "",
    }


class SessionLifecycleSupervisor:
    def __init__(
        self,
        *,
        attempt_id: str,
        agent_id: str,
        session_id: str,
        config_path: Path,
        evidence_path: Path,
    ) -> None:
        self.attempt_id = attempt_id
        self.agent_id = agent_id
        self.session_id = session_id
        self.config_path = config_path
        self.evidence_path = evidence_path
        self.journal_path = evidence_path.with_name("session-lifecycle.events.jsonl")
        self.session_dir = session_store_path_for_agent(agent_id, config_path=config_path).parent
        self.owner_token = uuid.uuid4().hex
        self.wrapper_pid = os.getpid()
        self.lock_path = self.session_dir / f".{session_id}.benchmark-owner.lock"
        self._lock_handle: Any = None
        self._sequence = 0
        self._followup_count = 0
        self._events: list[dict[str, Any]] = []
        self._invocations: list[dict[str, Any]] = []
        self._takeover_detected = False
        self._takeover_reasons: list[str] = []
        self._cleanup: dict[str, Any] = {"status": "pending"}
        self._final_status = "running"
        self._primary_snapshot: dict[str, Any] | None = None

    @classmethod
    def from_environment(
        cls,
        *,
        agent_id: str,
        session_id: str,
        config_path: Path,
        env: dict[str, str],
    ) -> SessionLifecycleSupervisor:
        workspace = Path(str(env.get("BENCHMARK_WORKSPACE_DIR") or config_path.parent)).expanduser().resolve()
        identity_text = str(env.get("BENCHMARK_ATTEMPT_IDENTITY") or "")
        try:
            identity = json.loads(identity_text) if identity_text else {}
        except json.JSONDecodeError:
            identity = {}
        attempt_id = "/".join(
            str(identity.get(key) or "") for key in ("run_id", "group_id", "record_id", "attempt_index")
        ).strip("/") or session_id
        return cls(
            attempt_id=attempt_id,
            agent_id=agent_id,
            session_id=session_id,
            config_path=config_path,
            evidence_path=workspace / "scratch" / "notes" / "session-lifecycle.json",
        )

    def __enter__(self) -> SessionLifecycleSupervisor:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise SessionOwnershipError(f"Session `{self.session_id}` already has an active benchmark owner.") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(self.owner_token)
        handle.flush()
        os.fsync(handle.fileno())
        self._lock_handle = handle
        self._record("owner_acquired", session_id=self.session_id, session_path=str(self.session_path(self.session_id)))
        return self

    def __exit__(self, exc_type, exc, _traceback) -> None:
        self.finalize(status="error" if exc is not None else self._final_status, error=str(exc or ""))

    def session_path(self, session_id: str) -> Path:
        return self.session_dir / f"{session_id}.jsonl"

    def allocate_followup_session(self, kind: str) -> str:
        self._followup_count += 1
        normalized = re.sub(r"[^a-z0-9_-]+", "-", kind.lower()).strip("-") or "followup"
        session_id = f"{self.session_id}-{normalized}-{self._followup_count}"
        self._record("followup_allocated", kind=kind, session_id=session_id, session_path=str(self.session_path(session_id)))
        return session_id

    def freeze_primary_snapshot(self) -> dict[str, Any] | None:
        """Atomically freeze the primary transcript for follow-up consumers."""
        path = self.session_path(self.session_id)
        if not path.is_file() or path.is_symlink():
            return None
        snapshot_path = self.evidence_path.parent / "session-snapshots" / f"{self.session_id}.primary.jsonl"
        _atomic_copy(path, snapshot_path)
        data = snapshot_path.read_bytes()
        meta = {"source_session_id": self.session_id, "path": str(snapshot_path), "sha256": hashlib.sha256(data).hexdigest(), "byte_size": len(data), "source_fingerprint": session_file_fingerprint(path).to_meta(), "frozen_at": _now()}
        self._primary_snapshot = meta
        self._record("primary_snapshot_frozen", **meta)
        return meta

    def invocation_started(self, *, kind: str, session_id: str, child_pid: int) -> None:
        invocation = {
            "kind": kind,
            "session_id": session_id,
            "session_path": str(self.session_path(session_id)),
            "openclaw_pid": child_pid,
            "started_at": _now(),
            "start_fingerprint": session_file_fingerprint(self.session_path(session_id)).to_meta(),
        }
        self._invocations.append(invocation)
        self._record("openclaw_started", **invocation)

    def invocation_finished(self, *, session_id: str, returncode: int, stdout: str, stderr: str) -> None:
        invocation = next(item for item in reversed(self._invocations) if item["session_id"] == session_id)
        invocation.update(
            {
                "ended_at": _now(),
                "exit_code": returncode,
                "end_fingerprint": session_file_fingerprint(self.session_path(session_id)).to_meta(),
                "stderr_excerpt": str(stderr or "")[:2000],
                "provider": provider_observability(self.session_path(session_id).with_suffix(".trajectory.jsonl")),
            }
        )
        diagnostic_text = "\n".join((str(stdout or ""), str(stderr or "")))
        if TAKEOVER_TEXT in diagnostic_text.lower():
            self._takeover_detected = True
            if "openclaw_reported_session_takeover" not in self._takeover_reasons:
                self._takeover_reasons.append("openclaw_reported_session_takeover")
        self._record("openclaw_finished", **invocation, takeover_detected=self._takeover_detected)

    def finalize(self, *, status: str, error: str = "") -> dict[str, Any]:
        if self._cleanup.get("status") == "complete":
            return self.to_meta()
        observed_owner = ""
        try:
            if self.lock_path.is_file():
                observed_owner = self.lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            observed_owner = ""
        if observed_owner != self.owner_token:
            self._takeover_detected = True
            if "owner_token_changed" not in self._takeover_reasons:
                self._takeover_reasons.append("owner_token_changed")
        snapshots: list[dict[str, Any]] = []
        for invocation in self._invocations:
            path = self.session_path(str(invocation["session_id"]))
            if not path.is_file() or path.is_symlink():
                continue
            snapshot_path = self.evidence_path.parent / "session-snapshots" / path.name
            _atomic_copy(path, snapshot_path)
            snapshots.append({"session_id": invocation["session_id"], "path": str(snapshot_path), "fingerprint": session_file_fingerprint(snapshot_path).to_meta()})
        self._final_status = "session_takeover" if self._takeover_detected else status
        self._cleanup = {"status": "complete", "owner_lock_released": False, "owner_lock_retained": True}
        if self._lock_handle is not None:
            self._lock_handle.seek(0)
            self._lock_handle.truncate()
            self._lock_handle.flush()
            os.fsync(self._lock_handle.fileno())
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
            self._lock_handle.close()
            self._lock_handle = None
            self._cleanup["owner_lock_released"] = True
        self._record("owner_released", status=self._final_status, error=error[:2000], snapshots=snapshots, cleanup=self._cleanup)
        return self.to_meta()

    def to_meta(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "wrapper_pid": self.wrapper_pid,
            "owner_token": self.owner_token,
            "session_path": str(self.session_path(self.session_id)),
            "takeover_detected": self._takeover_detected,
            "takeover_reasons": list(self._takeover_reasons),
            "final_status": self._final_status,
            "invocations": list(self._invocations),
            "primary_snapshot": dict(self._primary_snapshot or {}),
            "events": list(self._events),
            "cleanup": dict(self._cleanup),
        }

    def _record(self, event: str, **details: Any) -> None:
        self._sequence += 1
        payload = {"sequence": self._sequence, "timestamp": _now(), "event": event, **details}
        self._events.append(payload)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_write_json(self.evidence_path, self.to_meta())


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()
