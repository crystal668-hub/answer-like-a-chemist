from dataclasses import asdict
import json

from benchmarking.core.reporting import GroupRecordResult, aggregate_results
from benchmarking.workflow.run_state import load_group_record_result, write_results_json_stream


def _item(index: int, *, failed: bool = False, cancelled: bool = False) -> GroupRecordResult:
    status = "cancelled" if cancelled else "failed" if failed else "completed"
    return GroupRecordResult(
        schema_version=3, group_id="g1" if index < 2 else "g2", group_label="G", runner="r",
        websearch=False, record_id=f"r{index}", subset="s1" if index % 2 == 0 else "s2",
        dataset="d", source_file="source", eval_kind="hle" if index == 2 else "generic",
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
    assert list(summary["groups"]["g1"]["by_subset"]) == ["s1", "s2"]
    assert summary["groups"]["g1"]["run_failed_count"] == 1
    assert summary["groups"]["g2"]["non_evaluable_count"] == 1


def test_stream_writer_upconverts_and_preserves_full_payload(tmp_path):
    item = _item(0)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(asdict(item)), encoding="utf-8")
    out = tmp_path / "results.json"
    write_results_json_stream(out, {"schema_version": 3, "summary": {"count": 1}, "errors": []}, [path])
    payload = json.loads(out.read_text(encoding="utf-8"))
    loaded = load_group_record_result(path)
    assert payload["results"][0] == asdict(loaded)
    assert payload["results"][0]["raw"] == {"raw": "preserved"}
    assert payload["results"][0]["full_response_text"] == "full answer with evidence"
