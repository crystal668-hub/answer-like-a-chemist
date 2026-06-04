from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class ProgressWriter:
    """Append benchmark progress events and keep a dashboard-friendly snapshot."""

    def __init__(self, output_root: str | Path, *, total_records: int, groups: list[str] | tuple[str, ...]) -> None:
        self.output_root = Path(output_root).expanduser().resolve()
        self.progress_root = self.output_root / "progress"
        self.events_path = self.progress_root / "events.jsonl"
        self.state_path = self.progress_root / "state.json"
        self.total = int(total_records)
        self.groups = [str(group) for group in groups]
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "status": "pending",
            "total": self.total,
            "completed": 0,
            "started_at": "",
            "updated_at": "",
            "completed_at": "",
            "groups": {
                group: {
                    "status": "pending",
                    "current_record_id": None,
                    "current_index": None,
                    "completed_records": [],
                    "completed_count": 0,
                    "started_at": "",
                    "updated_at": "",
                    "completed_at": "",
                    "errors": [],
                }
                for group in self.groups
            },
            "errors": [],
        }

    def _emit(self, event_type: str, **payload: Any) -> None:
        now = timestamp()
        event = {"type": event_type, "timestamp": now, **payload}
        self.progress_root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._state["updated_at"] = now
        _write_json(self.state_path, self._state)

    def _group(self, group_id: str) -> dict[str, Any]:
        groups = self._state.setdefault("groups", {})
        return groups.setdefault(
            group_id,
            {
                "status": "pending",
                "current_record_id": None,
                "current_index": None,
                "completed_records": [],
                "completed_count": 0,
                "started_at": "",
                "updated_at": "",
                "completed_at": "",
                "errors": [],
            },
        )

    def run_started(self) -> None:
        with self._lock:
            now = timestamp()
            self._state["status"] = "running"
            self._state["started_at"] = self._state.get("started_at") or now
            self._emit("run_started", total=self.total, groups=self.groups)

    def group_started(self, group_id: str) -> None:
        with self._lock:
            group = self._group(group_id)
            now = timestamp()
            group["status"] = "running"
            group["started_at"] = group.get("started_at") or now
            group["updated_at"] = now
            self._emit("group_started", group_id=group_id)

    def record_started(self, group_id: str, record_id: str, *, index: int | None = None) -> None:
        with self._lock:
            group = self._group(group_id)
            group["status"] = "running"
            group["current_record_id"] = record_id
            group["current_index"] = index
            group["updated_at"] = timestamp()
            self._emit("record_started", group_id=group_id, record_id=record_id, index=index)

    def record_completed(self, group_id: str, record_id: str, *, status: str, score: float | None = None) -> None:
        with self._lock:
            group = self._group(group_id)
            completed = list(group.get("completed_records") or [])
            if record_id not in completed:
                completed.append(record_id)
                self._state["completed"] = int(self._state.get("completed") or 0) + 1
            group["completed_records"] = completed
            group["completed_count"] = len(completed)
            group["current_record_id"] = None
            group["current_index"] = None
            group["updated_at"] = timestamp()
            self._emit("record_completed", group_id=group_id, record_id=record_id, status=status, score=score)

    def group_completed(self, group_id: str, *, status: str = "completed") -> None:
        with self._lock:
            group = self._group(group_id)
            now = timestamp()
            group["status"] = status
            group["current_record_id"] = None
            group["current_index"] = None
            group["updated_at"] = now
            group["completed_at"] = now
            self._emit("group_completed", group_id=group_id, status=status)

    def error(self, *, group_id: str | None = None, record_id: str | None = None, message: str) -> None:
        with self._lock:
            error = {"group_id": group_id, "record_id": record_id, "message": message, "timestamp": timestamp()}
            self._state.setdefault("errors", []).append(error)
            if group_id:
                self._group(group_id).setdefault("errors", []).append(error)
            self._emit("error", **error)

    def run_completed(self, *, status: str = "completed") -> None:
        with self._lock:
            now = timestamp()
            self._state["status"] = status
            self._state["updated_at"] = now
            self._state["completed_at"] = now
            self._emit("run_completed", status=status)


def _fallback_group_progress(run_root: Path, group_id: str) -> dict[str, Any]:
    group_dir = run_root / "per-record" / group_id
    records = sorted(path.stem for path in group_dir.glob("*.json")) if group_dir.is_dir() else []
    return {
        "status": "completed" if records else "pending",
        "current_record_id": None,
        "current_index": None,
        "completed_records": records,
        "completed_count": len(records),
        "started_at": "",
        "updated_at": "",
        "completed_at": "",
        "errors": [],
    }


def _fallback_status(run_root: Path) -> str:
    progress_state = run_root / "progress" / "state.json"
    if progress_state.is_file():
        try:
            return str((_read_json(progress_state) or {}).get("status") or "unknown")
        except Exception:
            pass
    wave_root = run_root / "waves"
    if wave_root.is_dir():
        statuses = []
        for path in sorted(wave_root.glob("*.json")):
            try:
                statuses.append(str((_read_json(path) or {}).get("status") or ""))
            except Exception:
                continue
        if any(status == "running" for status in statuses):
            return "running"
        if statuses and all(status == "completed" for status in statuses):
            return "completed"
    if (run_root / "results.json").is_file():
        return "completed"
    return "pending"


def load_progress(
    run_root: str | Path,
    *,
    expected_total: int | None = None,
    group_ids: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    root = Path(run_root).expanduser().resolve()
    state_path = root / "progress" / "state.json"
    if state_path.is_file():
        payload = _read_json(state_path)
        if isinstance(payload, dict):
            payload.setdefault("source", "progress_state")
            return payload

    groups = list(group_ids or [])
    if not groups:
        per_record_root = root / "per-record"
        groups = sorted(path.name for path in per_record_root.iterdir() if path.is_dir()) if per_record_root.is_dir() else []
    group_payload = {group_id: _fallback_group_progress(root, group_id) for group_id in groups}
    completed = sum(int(group.get("completed_count") or 0) for group in group_payload.values())
    total = max(int(expected_total), completed) if expected_total is not None else completed
    status = _fallback_status(root)
    if status == "running":
        for group in group_payload.values():
            if group["status"] == "pending":
                group["status"] = "running"
    return {
        "source": "fallback",
        "status": status,
        "total": total,
        "completed": completed,
        "started_at": "",
        "updated_at": "",
        "completed_at": "",
        "groups": group_payload,
        "errors": [],
    }
