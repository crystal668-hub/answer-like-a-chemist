from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from benchmarking.runtime.observability import (
    observe_transcript_decode,
    observe_transcript_snapshot,
)


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    line_number: int
    payload: Any


@dataclass(frozen=True, slots=True)
class TranscriptParseFailure:
    line_number: int
    exception_type: str
    message: str
    line_sha256: str


class TranscriptIndexError(ValueError):
    def __init__(self, failure: TranscriptParseFailure) -> None:
        super().__init__(
            f"transcript JSON decode failed at line {failure.line_number}: {failure.message}"
        )
        self.failure = failure


@dataclass(frozen=True, slots=True)
class TranscriptIndex:
    """Disposable parsed view of one immutable transcript snapshot."""

    path: Path
    sha256: str
    byte_count: int
    line_count: int
    entries: tuple[TranscriptEntry, ...]
    parse_failures: tuple[TranscriptParseFailure, ...]

    @classmethod
    def from_path(cls, path: str | Path, *, errors: str = "strict") -> TranscriptIndex:
        source = Path(path).expanduser()
        raw = source.read_bytes()
        byte_count = len(raw)
        fingerprint = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8", errors=errors)
        del raw
        entries: list[TranscriptEntry] = []
        failures: list[TranscriptParseFailure] = []
        lines = text.splitlines()
        del text
        line_count = len(lines)
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                observe_transcript_decode(succeeded=False)
                failures.append(
                    TranscriptParseFailure(
                        line_number=line_number,
                        exception_type=type(exc).__name__,
                        message=exc.msg,
                        line_sha256=hashlib.sha256(line.encode("utf-8")).hexdigest(),
                    )
                )
                continue
            observe_transcript_decode(succeeded=True)
            entries.append(TranscriptEntry(line_number=line_number, payload=payload))
        del lines
        observe_transcript_snapshot(byte_count=byte_count, line_count=line_count)
        return cls(
            path=source,
            sha256=fingerprint,
            byte_count=byte_count,
            line_count=line_count,
            entries=tuple(entries),
            parse_failures=tuple(failures),
        )

    def strict_payloads(
        self,
        *,
        mappings: Mapping[str, str] | None = None,
        projector: Callable[[Any, Mapping[str, str]], Any] | None = None,
    ) -> list[tuple[int, Any]]:
        if self.parse_failures:
            raise TranscriptIndexError(self.parse_failures[0])
        if mappings and projector is not None:
            return [
                (entry.line_number, projector(entry.payload, mappings))
                for entry in self.entries
            ]
        return [(entry.line_number, entry.payload) for entry in self.entries]

    def dict_events(self) -> tuple[dict[str, Any], ...]:
        return tuple(entry.payload for entry in self.entries if isinstance(entry.payload, dict))

    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            event["message"]
            for event in self.dict_events()
            if isinstance(event.get("message"), dict)
        )

    def to_meta(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "byte_count": self.byte_count,
            "line_count": self.line_count,
            "json_decode_count": len(self.entries) + len(self.parse_failures),
            "parse_failures": [
                {
                    "line_number": failure.line_number,
                    "exception_type": failure.exception_type,
                    "message": failure.message,
                    "line_sha256": failure.line_sha256,
                }
                for failure in self.parse_failures
            ],
        }
