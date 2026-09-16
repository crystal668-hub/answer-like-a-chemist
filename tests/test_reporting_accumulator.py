import gc
import weakref

from benchmarking.core.reporting import AggregateAccumulator, GroupRecordResult


def _item(*, passed=True, scored=True, eval_kind="chembench", subset="all", elapsed=2.0):
    return GroupRecordResult(
        schema_version=3, group_id="g", group_label="G", runner="r", websearch=False,
        record_id=str(elapsed), subset=subset, dataset="d", source_file="s", eval_kind=eval_kind,
        prompt="p", reference_answer="a", answer_text="a", evaluation={"passed": passed, "score": 1.0 if passed else 0.0, "normalized_score": 1.0 if passed else 0.0, "details": {}},
        runner_meta={}, raw={}, elapsed_seconds=elapsed, run_lifecycle_status="completed",
        protocol_completion_status="completed", protocol_acceptance_status=None, answer_availability="available",
        answer_reliability="native", evaluable=True, scored=scored, recovery_mode="none", degraded_execution=False,
    )


def test_incremental_accumulator_matches_bucket():
    items = [_item(), _item(passed=False, scored=False, elapsed=4.0)]
    acc = AggregateAccumulator()
    for item in items:
        acc.add(item)
    actual = acc.to_dict()
    assert actual["count"] == 2
    assert actual["pass_count"] == 1
    assert actual["scored_count"] == 1
    assert actual["avg_score"] == 1.0
    assert actual["avg_normalized_score"] == 1.0
    assert actual["avg_elapsed_seconds"] == 3.0
    assert actual["run_completed_count"] == 2


def test_accumulator_does_not_retain_records():
    acc = AggregateAccumulator()
    item = _item()
    reference = weakref.ref(item)
    acc.add(item)
    del item
    gc.collect()
    assert reference() is None
    assert acc.to_dict()["count"] == 1
