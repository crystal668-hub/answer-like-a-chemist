from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _deep_copy_jsonish(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _deep_copy_jsonish(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy_jsonish(item) for item in value]
    if isinstance(value, tuple):
        return [_deep_copy_jsonish(item) for item in value]
    return value


def _grading_config_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "preferred_score": _deep_copy_jsonish(payload.get("preferred_score")),
        "relative_tolerance": _deep_copy_jsonish(payload.get("relative_tolerance")),
        "options": _deep_copy_jsonish(payload.get("options")),
        "reference_reasoning": _deep_copy_jsonish(payload.get("reference_reasoning")),
        "verifier_grounded": _deep_copy_jsonish(payload.get("verifier_grounded")),
    }


@dataclass(frozen=True)
class GradingSpec:
    kind: str
    reference_answer: str
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", _deep_copy_jsonish(self.config))


class RecordValidationError(ValueError):
    pass


@dataclass
class BenchmarkRecord:
    record_id: str
    track: str
    source_file: str
    eval_kind: str
    prompt: str
    reference_answer: str
    payload: dict[str, Any]

    def __init__(
        self,
        *,
        record_id: str,
        track: str,
        source_file: str,
        prompt: str,
        grading: GradingSpec | None = None,
        raw_payload: dict[str, Any] | None = None,
        eval_kind: str | None = None,
        reference_answer: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        resolved_payload = _deep_copy_jsonish(payload if payload is not None else (raw_payload or {}))
        resolved_kind = str(
            eval_kind
            if eval_kind is not None
            else (grading.kind if grading is not None else resolved_payload.get("eval_kind") or "")
        ).strip()
        resolved_reference = str(
            reference_answer
            if reference_answer is not None
            else (
                grading.reference_answer
                if grading is not None
                else (resolved_payload.get("answer") or resolved_payload.get("target") or "")
            )
        ).strip()
        self.record_id = record_id
        self.track = track
        self.source_file = source_file
        self.eval_kind = resolved_kind
        self.prompt = prompt
        self.reference_answer = resolved_reference
        self.payload = resolved_payload
        self._grading = grading or GradingSpec(
            kind=resolved_kind,
            reference_answer=resolved_reference,
            config=_grading_config_from_payload(resolved_payload),
        )

    @property
    def raw_payload(self) -> dict[str, Any]:
        return self.payload

    @property
    def grading(self) -> GradingSpec:
        return self._grading


def track_name_from_file(path: Path) -> str:
    path = Path(path)
    if path.parent.name == "tracks":
        return path.stem
    if path.parent.name != "data":
        return ""
    return path.parent.parent.name


def track_from_payload(payload: dict[str, Any]) -> str:
    verifier = payload.get("verifier_grounded")
    if not isinstance(verifier, dict):
        return ""
    return str(verifier.get("track") or "").strip()


def build_grading_spec(*, track: str, payload: dict[str, Any]) -> GradingSpec:
    record_id = str(payload.get("id") or track)
    reference_answer = str(payload.get("answer") or payload.get("target") or "").strip()
    if not reference_answer:
        raise RecordValidationError(f"Missing answer/target field in record: {record_id}")
    kind = str(payload.get("eval_kind") or "").strip()
    if kind != "verifier_grounded":
        raise RecordValidationError(
            f"Unsupported eval_kind for record {record_id!r}: {kind or '<missing>'}"
        )
    payload_track = track_from_payload(payload)
    if not payload_track:
        raise RecordValidationError(f"Missing verifier_grounded.track in record: {record_id}")
    if payload_track != track:
        raise RecordValidationError(
            f"Record track {payload_track!r} does not match source track {track!r}: {record_id}"
        )
    return GradingSpec(
        kind=kind,
        reference_answer=reference_answer,
        config=_grading_config_from_payload(payload),
    )


def load_records(paths: Iterable[Path]) -> list[BenchmarkRecord]:
    records: list[BenchmarkRecord] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser().resolve()
        track = track_name_from_file(path)
        if not track or path.name != f"{track}.jsonl":
            raise RecordValidationError(
                f"Benchmark file must use <track>/data/<track>.jsonl layout: {path}"
            )
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise RecordValidationError(f"Benchmark record must be a JSON object: {path}")
                record_id = str(payload.get("id") or f"{track}-{len(records)}")
                prompt = str(payload.get("prompt") or "").strip()
                if not prompt:
                    raise RecordValidationError(f"Missing prompt field in record: {record_id}")
                grading = build_grading_spec(track=track, payload=payload)
                records.append(
                    BenchmarkRecord(
                        record_id=record_id,
                        track=track,
                        source_file=str(path),
                        prompt=prompt,
                        grading=grading,
                        raw_payload=payload,
                    )
                )
    return records
