from benchmarking.core.reporting import AggregateAccumulator, GroupRecordResult, aggregate_bucket


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
    assert acc.to_dict() == aggregate_bucket(items)


def test_accumulator_does_not_retain_records():
    acc = AggregateAccumulator()
    acc.add(_item())
    assert not hasattr(acc, "items")
    assert acc.to_dict()["count"] == 1
