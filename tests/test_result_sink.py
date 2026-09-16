from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.runtime import atomic_io
from benchmarking.workflow.run_state import ResultSink, pending_records_for_group


def test_atomic_write_failure_preserves_previous_target(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "state.json"
    target.write_text('{"version":1}\n', encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        atomic_io.atomic_write_json(target, {"version": 2})

    assert target.read_text(encoding="utf-8") == '{"version":1}\n'
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_result_sink_skips_unchanged_payload_and_rewrites_changed_reference(tmp_path: Path) -> None:
    sink = ResultSink(tmp_path)
    path = tmp_path / "per-record/g/r1.json"
    payload = {"record_id": "r1", "reference_answer": "old"}

    sink.save_json(path, payload)
    sink.save_json(path, dict(payload))
    changed = {**payload, "reference_answer": "public-gold"}
    sink.save_json(path, changed)

    assert json.loads(path.read_text(encoding="utf-8")) == changed
    assert sink.to_meta() == {"write_count": 2, "unchanged_count": 1}


def test_per_record_commit_is_resume_source_before_results_aggregate(tmp_path: Path) -> None:
    sink = ResultSink(tmp_path)
    path = tmp_path / "per-record/single_llm_skills_on/r1.json"
    sink.save_json(path, {"record_id": "r1", "status": "completed"})
    record = BenchmarkRecord(
        record_id="r1",
        dataset="demo",
        source_file="demo.jsonl",
        eval_kind="generic_semantic",
        prompt="Q",
        reference_answer="A",
    )

    pending = pending_records_for_group(
        [record],
        output_root=tmp_path,
        group_id="single_llm_skills_on",
        merge_existing_per_record=True,
    )

    assert pending == []
    assert not (tmp_path / "results.json").exists()


def test_result_sink_rejects_symlink_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    path = tmp_path / "per-record/g/r1.json"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(OSError, match="symlink"):
        ResultSink(tmp_path).save_json(path, {"record_id": "r1"})

    assert outside.read_text(encoding="utf-8") == "{}\n"


def test_result_sink_rejects_symlinked_per_record_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "per-record").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="symlink"):
        ResultSink(tmp_path).save_json(
            tmp_path / "per-record/g/r1.json",
            {"record_id": "r1"},
        )

    assert list(outside.rglob("*")) == []
