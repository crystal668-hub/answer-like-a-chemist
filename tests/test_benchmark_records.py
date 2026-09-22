from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from benchmarking.core.records import BenchmarkRecord, GradingSpec, load_records
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import (
    EVALUATORS,
    evaluate_record,
    register_evaluator,
)


def _write_record(path: Path, *, track: str = "open_generation_rdkit", **overrides: object) -> Path:
    payload = {
        "id": "task-1",
        "prompt": "Question?",
        "answer": "No reference answer is exposed.",
        "eval_kind": "verifier_grounded",
        "verifier_grounded": {"track": track, "task_id": "task-1"},
        **overrides,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("track", ["open_generation_rdkit", "open_generation_xtb"])
def test_load_records_builds_track_grading_spec(tmp_path: Path, track: str) -> None:
    path = _write_record(tmp_path / track / "data" / f"{track}.jsonl", track=track)

    record = load_records([path])[0]

    assert record.track == track
    assert record.grading.kind == "verifier_grounded"
    assert record.grading.config["verifier_grounded"]["track"] == track


def test_load_records_rejects_missing_prompt_answer_and_mismatched_track(tmp_path: Path) -> None:
    path = tmp_path / "open_generation_rdkit" / "data" / "open_generation_rdkit.jsonl"
    _write_record(path, prompt="")
    with pytest.raises(ValueError, match="Missing prompt"):
        load_records([path])

    _write_record(path, answer="", target="")
    with pytest.raises(ValueError, match="Missing answer/target"):
        load_records([path])

    _write_record(path, track="open_generation_xtb")
    with pytest.raises(ValueError, match="does not match source track"):
        load_records([path])


def test_evaluate_record_uses_explicit_registry_dispatch() -> None:
    calls: list[str] = []

    def evaluator(record: BenchmarkRecord, **_kwargs: object) -> dict[str, bool]:
        calls.append(record.record_id)
        return {"ok": True}

    saved = dict(EVALUATORS)
    try:
        register_evaluator("unit_test", evaluator)
        record = BenchmarkRecord(
            record_id="task-1",
            track="open_generation_rdkit",
            source_file="fixture",
            prompt="Question?",
            grading=GradingSpec(kind="unit_test", reference_answer="42"),
        )
        assert evaluate_record(
            record,
            short_answer_text="42",
            full_response_text="FINAL ANSWER: 42",
            judge=None,
        ) == {"ok": True}
        assert calls == ["task-1"]
    finally:
        EVALUATORS.clear()
        EVALUATORS.update(saved)


def test_benchmark_record_serializes_track_only_and_deep_copies_payload() -> None:
    payload = {"options": {"A": "x"}}
    record = BenchmarkRecord(
        record_id="task-1",
        track="open_generation_rdkit",
        source_file="fixture",
        prompt="Question?",
        eval_kind="verifier_grounded",
        reference_answer="hidden",
        payload=payload,
    )
    record.payload["options"]["A"] = "changed"

    serialized = asdict(record)
    assert set(serialized) == {
        "record_id", "track", "source_file", "eval_kind", "prompt", "reference_answer", "payload"
    }
    assert "dataset" not in serialized and "subset" not in serialized
    assert record.grading.config["options"]["A"] == "x"
    assert payload["options"]["A"] == "x"


def test_evaluate_record_unknown_kind_has_no_generic_fallback() -> None:
    record = BenchmarkRecord(
        record_id="task-1",
        track="open_generation_rdkit",
        source_file="fixture",
        prompt="Question?",
        grading=GradingSpec(kind="missing", reference_answer="42"),
    )
    with pytest.raises(EvaluationRegistryError, match="No evaluator"):
        evaluate_record(
            record,
            short_answer_text="42",
            full_response_text="FINAL ANSWER: 42",
            judge=None,
            evaluators={},
        )
