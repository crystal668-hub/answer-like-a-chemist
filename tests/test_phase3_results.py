import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from benchmarking.core.reporting import (
    GroupRecordResult,
    aggregate_bucket,
    aggregate_results,
)
from benchmarking.runtime import atomic_io
from benchmarking.workflow import cli
from benchmarking.workflow.orchestration import PersistedResultRef
from benchmarking.workflow.run_state import (
    load_group_record_result,
    write_results_json_stream,
)


def _item(index: int, *, failed: bool = False, cancelled: bool = False) -> GroupRecordResult:
    status = "cancelled" if cancelled else "failed" if failed else "completed"
    return GroupRecordResult(
        schema_version=4, group_id="g1" if index < 2 else "g2", group_label="G", runner="r",
        websearch=False, record_id=f"r{index}", track="s1" if index % 2 == 0 else "s2",
        source_file="source", eval_kind="hle" if index == 2 else "generic",
        prompt="p", reference_answer="a", answer_text="full answer with evidence",
        evaluation={"passed": not failed and not cancelled, "score": 0.0 if failed else 1.0,
                    "normalized_score": 0.0 if failed else 1.0, "details": {"confidence": 80} if index == 2 else {}},
        runner_meta={"audit": "preserved"}, raw={"raw": "preserved"}, elapsed_seconds=float(index + 1),
        run_lifecycle_status=status, protocol_completion_status="missing" if cancelled else "completed",
        protocol_acceptance_status=None, answer_availability="missing" if cancelled else "native_final",
        answer_reliability="none" if cancelled else "native", evaluable=not failed and not cancelled,
        scored=not failed and not cancelled, recovery_mode="none", degraded_execution=failed,
        error="failed" if failed else None, short_answer_text="short", full_response_text="full answer with evidence",
    )


def test_aggregate_results_accepts_single_pass_iterable_with_order_and_failures():
    items = [_item(0), _item(1, failed=True), _item(2, cancelled=True)]
    summary = aggregate_results((item for item in items))
    assert summary["group_order"] == ["g1", "g2"]
    assert list(summary["groups"]["g1"]["by_track"]) == ["s1", "s2"]
    assert summary["groups"]["g1"]["run_failed_count"] == 1
    assert summary["groups"]["g2"]["non_evaluable_count"] == 1


def _legacy_aggregate_results(items):
    grouped = {}
    for item in items:
        grouped.setdefault(item.group_id, []).append(item)
    groups = {}
    group_track = {}
    for group_id, group_items in grouped.items():
        by_eval_kind = {}
        by_track = {}
        for item in group_items:
            by_eval_kind.setdefault(item.eval_kind, []).append(item)
            by_track.setdefault(item.track, []).append(item)
        meta = {
            "group_label": group_items[0].group_label,
            "runner": group_items[0].runner,
            "websearch": group_items[0].websearch,
            "skills_enabled": group_items[0].skills_enabled,
        }
        groups[group_id] = {
            **meta,
            **aggregate_bucket(group_items),
            "by_eval_kind": {key: aggregate_bucket(value) for key, value in by_eval_kind.items()},
            "by_track": {key: aggregate_bucket(value) for key, value in by_track.items()},
        }
        for track, track_items in by_track.items():
            group_track[f"{group_id}::{track}"] = {
                "group_id": group_id,
                **meta,
                "track": track,
                **aggregate_bucket(track_items),
            }
    return {"group_order": list(grouped), "groups": groups, "group_track": group_track}


def test_streaming_aggregate_matches_legacy_summary_in_full():
    items = [_item(2, cancelled=True), _item(0), _item(1, failed=True)]
    items[1].runner_meta = {
        "skill_use_audit": {"exec_tool_call_count": 2, "tool_result_error_count": 1},
        "workspace_isolation": {
            "preflight_ok": True,
            "audit_execution_status": "complete",
            "boundary_status": "warning",
            "contamination_status": "clear",
            "adjudication": "scoreable_degraded",
            "archive_ok": True,
            "cleanup": {"failed_count": 0},
        },
    }
    expected = _legacy_aggregate_results(items)
    actual = aggregate_results(iter(items))
    assert actual == expected
    assert list(actual["groups"]) == ["g2", "g1"]
    assert list(actual["groups"]["g1"]["by_eval_kind"]) == ["generic"]
    assert list(actual["groups"]["g1"]["by_track"]) == ["s1", "s2"]


def test_stream_writer_upconverts_and_preserves_full_payload(tmp_path):
    item = _item(0)
    historical = asdict(item)
    for key in (
        "schema_version", "run_lifecycle_status", "protocol_completion_status",
        "protocol_acceptance_status", "answer_availability", "answer_reliability",
        "evaluable", "scored", "recovery_mode", "degraded_execution",
        "skills_enabled", "execution_error_kind",
    ):
        historical.pop(key)
    historical["runner_meta"]["scored"] = True
    path = tmp_path / "r.json"
    path.write_text(json.dumps(historical), encoding="utf-8")
    out = tmp_path / "results.json"
    write_results_json_stream(out, {"schema_version": 4, "summary": {"count": 1}, "errors": []}, [path])
    payload = json.loads(out.read_text(encoding="utf-8"))
    loaded = load_group_record_result(path)
    assert payload["results"][0] == asdict(loaded)
    assert payload["results"][0]["schema_version"] == 4
    assert payload["results"][0]["raw"] == {"raw": "preserved"}
    assert payload["results"][0]["full_response_text"] == "full answer with evidence"
    assert out.read_text(encoding="utf-8") == json.dumps(
        {"schema_version": 4, "summary": {"count": 1}, "errors": [], "results": [asdict(loaded)]},
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def test_result_path_selection_preserves_merge_and_current_run_contracts(tmp_path):
    for group_id, names in (("g1", ("z.json", "a.json")), ("g2", ("m.json",))):
        directory = tmp_path / "per-record" / group_id
        directory.mkdir(parents=True)
        for name in names:
            (directory / name).write_text("{}", encoding="utf-8")
    merged = cli._result_paths_for_aggregation(
        output_root=tmp_path,
        selected_group_ids=["g1"],
        aggregate_group_ids=["g2", "g1"],
        group_results={},
        records=[],
        merge_existing_per_record=True,
    )
    assert [(path.parent.name, path.name) for path in merged] == [
        ("g2", "m.json"), ("g1", "a.json"), ("g1", "z.json")
    ]

    current_a = tmp_path / "current-a.json"
    current_b = tmp_path / "current-b.json"
    refs = {
        "g1": [
            PersistedResultRef("g1", "b", "completed", path=str(current_b)),
            PersistedResultRef("g1", "a", "completed", path=str(current_a)),
        ]
    }
    current = cli._result_paths_for_aggregation(
        output_root=tmp_path,
        selected_group_ids=["g1"],
        aggregate_group_ids=["g1", "g2"],
        group_results=refs,
        records=[SimpleNamespace(record_id="a"), SimpleNamespace(record_id="b")],
        merge_existing_per_record=False,
    )
    assert current == [current_a, current_b]


@pytest.mark.parametrize("archive_error", [None, "", {}])
def test_cancelled_archive_failure_survives_lightweight_reference(tmp_path, archive_error):
    item = replace(
        _item(0, cancelled=True),
        runner_meta={"workspace_isolation": {"archive_ok": False, "archive_error": archive_error}},
    )
    ref = cli._persisted_ref_for_entry(item, tmp_path)
    assert ref.archive_failed is True
    assert cli._cancelled_result_errors([ref]) == [{
        "stage": "workspace_seal",
        "group_id": "g1",
        "record_id": "r0",
        "error": "workspace archive failed",
    }]


def test_cancelled_archive_success_and_cleanup_failure_are_independent(tmp_path):
    item = replace(
        _item(0, cancelled=True),
        runner_meta={"workspace_isolation": {
            "archive_ok": True,
            "archive_error": "stale diagnostic",
            "cleanup": {"failed_count": 2},
        }},
    )
    errors = cli._cancelled_result_errors([cli._persisted_ref_for_entry(item, tmp_path)])
    assert errors == [{
        "stage": "workspace_cleanup",
        "group_id": "g1",
        "record_id": "r0",
        "failed_count": 2,
    }]


def _assert_old_results_survive(out, tmp_path):
    assert out.read_text(encoding="utf-8") == "old results\n"
    assert list(tmp_path.glob(".results.json.*.tmp")) == []


def test_stream_writer_read_failure_keeps_old_results_and_can_rebuild(tmp_path):
    out = tmp_path / "results.json"
    out.write_text("old results\n", encoding="utf-8")
    record = tmp_path / "record.json"
    with pytest.raises(FileNotFoundError):
        write_results_json_stream(out, {"schema_version": 4}, [record])
    _assert_old_results_survive(out, tmp_path)
    record.write_text(json.dumps(asdict(_item(0))), encoding="utf-8")
    write_results_json_stream(out, {"schema_version": 4}, [record])
    assert json.loads(out.read_text(encoding="utf-8"))["results"][0]["record_id"] == "r0"


def test_stream_writer_encoding_failure_keeps_old_results(monkeypatch, tmp_path):
    out = tmp_path / "results.json"
    out.write_text("old results\n", encoding="utf-8")
    record = tmp_path / "record.json"
    record.write_text(json.dumps(asdict(_item(0))), encoding="utf-8")
    original = json.dumps

    def fail_record(payload, *args, **kwargs):
        if isinstance(payload, dict) and payload.get("record_id") == "r0":
            raise TypeError("injected encoding failure")
        return original(payload, *args, **kwargs)

    monkeypatch.setattr("benchmarking.workflow.run_state.json.dumps", fail_record)
    with pytest.raises(TypeError, match="injected encoding failure"):
        write_results_json_stream(out, {"schema_version": 4}, [record])
    _assert_old_results_survive(out, tmp_path)


def test_stream_writer_replace_failure_keeps_old_results(monkeypatch, tmp_path):
    out = tmp_path / "results.json"
    out.write_text("old results\n", encoding="utf-8")
    record = tmp_path / "record.json"
    record.write_text(json.dumps(asdict(_item(0))), encoding="utf-8")

    def fail_replace(source, target):
        raise OSError("injected replace failure")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        write_results_json_stream(out, {"schema_version": 4}, [record])
    _assert_old_results_survive(out, tmp_path)
