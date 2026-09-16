"""Restricted, deterministic context extraction for finalization rescue turns."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarking.runtime.transcript_index import TranscriptIndex

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)")
_ABS_PATH_RE = re.compile(r"(?:/Users|/home|/tmp|/var|/opt|/benchmark)[^\s\"']*")

@dataclass(frozen=True)
class FinalizationContextBundle:
    schema_version: int
    source_session_id: str
    source_snapshot_sha256: str
    eval_kind: str
    answer_schema: dict[str, Any]
    original_task: str
    primary_native_output: str
    evidence_events: list[dict[str, Any]]
    omitted_event_count: int
    included_chars: int
    max_chars: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_projection(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if not _SECRET_RE.search(str(k))}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return _ABS_PATH_RE.sub("[path omitted]", value)
    return value

def build_finalization_context_bundle(
    snapshot_path: Path,
    *, source_session_id: str, eval_kind: str, answer_schema: dict[str, Any],
    original_task: str, primary_native_output: str, max_chars: int = 12000,
) -> FinalizationContextBundle:
    required = [str(original_task or ""), str(eval_kind or ""), json.dumps(answer_schema or {}, ensure_ascii=False)]
    if sum(len(x) for x in required) > max_chars:
        raise ValueError("finalization context required fields exceed character budget")
    index = TranscriptIndex.from_path(snapshot_path, errors="replace")
    digest = index.sha256
    events: list[dict[str, Any]] = []
    budget = max_chars - sum(len(x) for x in required)
    for entry in reversed(index.entries):
        item = entry.payload
        if not isinstance(item, dict):
            continue
        msg = item.get("message") if isinstance(item.get("message"), dict) else item
        role = str(msg.get("role") or "") if isinstance(msg, dict) else ""
        if role not in {"assistant", "tool"}:
            continue
        text = msg.get("content") if isinstance(msg, dict) else None
        event = {"role": role, "content": _clean(text)}
        call_id = msg.get("toolCallId") or msg.get("callId") if isinstance(msg, dict) else None
        if call_id:
            event["call_id"] = str(call_id)
        encoded = json.dumps(event, ensure_ascii=False)
        if len(encoded) > budget:
            continue
        events.insert(0, event)
        budget -= len(encoded)
    included = sum(len(json.dumps(e, ensure_ascii=False)) for e in events)
    return FinalizationContextBundle(1, source_session_id, digest, str(eval_kind or ""), _clean(answer_schema or {}), str(_clean(original_task or "")), str(_clean(primary_native_output or "")), events, max(0, index.line_count-len(events)), included, max_chars)

def write_finalization_context_bundle(bundle: FinalizationContextBundle, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(bundle.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp).replace(path)
    finally:
        Path(tmp).unlink(missing_ok=True)
