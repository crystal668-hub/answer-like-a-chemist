from __future__ import annotations

import math
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class GroupRecordResult:
    schema_version: int
    group_id: str
    group_label: str
    runner: str
    websearch: bool
    record_id: str
    subset: str
    dataset: str
    source_file: str
    eval_kind: str
    prompt: str
    reference_answer: str
    answer_text: str
    evaluation: dict[str, Any]
    runner_meta: dict[str, Any]
    raw: dict[str, Any]
    elapsed_seconds: float
    run_lifecycle_status: str
    protocol_completion_status: str
    protocol_acceptance_status: str | None
    answer_availability: str
    answer_reliability: str
    evaluable: bool
    scored: bool
    recovery_mode: str
    degraded_execution: bool
    skills_enabled: bool = False
    execution_error_kind: str | None = None
    error: str | None = None
    short_answer_text: str = ""
    full_response_text: str = ""


def average_optional_metric(items: list[GroupRecordResult], key: str) -> float | None:
    values: list[float] = []
    for item in items:
        details = item.evaluation.get("details") or {}
        value = details.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    if not values:
        return None
    return sum(values) / len(values)


def hle_calibration_rmse(items: list[GroupRecordResult]) -> float | None:
    squared_errors: list[float] = []
    for item in items:
        if item.eval_kind != "hle":
            continue
        details = item.evaluation.get("details") or {}
        confidence = details.get("confidence")
        if not isinstance(confidence, (int, float)):
            continue
        confidence_probability = max(0.0, min(100.0, float(confidence))) / 100.0
        correctness = 1.0 if item.evaluation.get("passed") else 0.0
        squared_errors.append((confidence_probability - correctness) ** 2)
    if not squared_errors:
        return None
    return math.sqrt(sum(squared_errors) / len(squared_errors))


def skill_audit(item: GroupRecordResult) -> dict[str, Any]:
    audit = (item.runner_meta or {}).get("skill_use_audit") or {}
    return audit if isinstance(audit, dict) else {}


def skill_tool_call_count(item: GroupRecordResult) -> int:
    if not item.skills_enabled:
        return 0
    value = skill_audit(item).get("skill_tool_call_count")
    return int(value) if isinstance(value, (int, float)) else 0


def skill_tool_failure_count(item: GroupRecordResult) -> int:
    if not item.skills_enabled:
        return 0
    value = skill_audit(item).get("skill_tool_failure_count")
    return int(value) if isinstance(value, (int, float)) else 0


def skill_audit_int(item: GroupRecordResult, key: str, *, skill_enabled_only: bool = False) -> int:
    if skill_enabled_only and not item.skills_enabled:
        return 0
    value = skill_audit(item).get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def exec_tool_call_count(item: GroupRecordResult) -> int:
    audit = skill_audit(item)
    value = audit.get("exec_tool_call_count")
    if not isinstance(value, (int, float)) and not item.skills_enabled:
        value = audit.get("skill_tool_call_count")
    return int(value) if isinstance(value, (int, float)) else 0


def exec_tool_failure_count(item: GroupRecordResult) -> int:
    audit = skill_audit(item)
    value = audit.get("exec_tool_failure_count")
    if not isinstance(value, (int, float)) and not item.skills_enabled:
        value = audit.get("skill_tool_failure_count")
    return int(value) if isinstance(value, (int, float)) else 0


def openclaw_tool_call_count(item: GroupRecordResult) -> int:
    audit = skill_audit(item)
    value = audit.get("openclaw_tool_call_count")
    if not isinstance(value, (int, float)):
        value = audit.get("tool_call_count")
    return int(value) if isinstance(value, (int, float)) else 0


def openclaw_tool_failure_count(item: GroupRecordResult) -> int:
    audit = skill_audit(item)
    value = audit.get("openclaw_tool_failure_count")
    if not isinstance(value, (int, float)):
        value = audit.get("tool_failure_count")
    return int(value) if isinstance(value, (int, float)) else 0


def session_isolation_audit(item: GroupRecordResult) -> dict[str, Any]:
    audit = (item.runner_meta or {}).get("session_isolation") or {}
    return audit if isinstance(audit, dict) else {}


def session_isolation_failed(item: GroupRecordResult) -> bool:
    audit = session_isolation_audit(item)
    return audit.get("session_isolation_ok") is False


def session_contaminated(item: GroupRecordResult) -> bool:
    audit = session_isolation_audit(item)
    if audit.get("session_isolation_ok") is not False:
        return False
    requested = str(audit.get("requested_session_id") or "").strip()
    actual = str(audit.get("postflight_entry_session_id") or "").strip()
    return bool(actual and actual != requested)


def workspace_isolation_audit(item: GroupRecordResult) -> dict[str, Any]:
    audit = (item.runner_meta or {}).get("workspace_isolation") or {}
    return audit if isinstance(audit, dict) else {}


def workspace_isolation_ok(item: GroupRecordResult) -> bool:
    audit = workspace_isolation_audit(item)
    return bool(
        audit
        and audit.get("preflight_ok") is True
        and audit.get("audit_status") == "clean"
        and audit.get("archive_ok") is True
        and audit.get("contaminated") is not True
    )


def workspace_isolation_failed(item: GroupRecordResult) -> bool:
    audit = workspace_isolation_audit(item)
    return bool(audit and not workspace_isolation_ok(item))


def aggregate_bucket(items: list[GroupRecordResult]) -> dict[str, Any]:
    return {
        "count": len(items),
        "pass_count": sum(1 for item in items if item.evaluation["passed"]),
        "run_completed_count": sum(1 for item in items if item.run_lifecycle_status == "completed"),
        "run_failed_count": sum(1 for item in items if item.run_lifecycle_status == "failed"),
        "protocol_completed_count": sum(1 for item in items if item.protocol_completion_status == "completed"),
        "protocol_failed_count": sum(1 for item in items if item.protocol_completion_status == "failed"),
        "evaluable_count": sum(1 for item in items if item.evaluable),
        "scored_count": sum(1 for item in items if item.scored),
        "recovered_evaluable_count": sum(1 for item in items if item.evaluable and item.recovery_mode != "none"),
        "native_evaluable_count": sum(1 for item in items if item.evaluable and item.recovery_mode == "none"),
        "non_evaluable_count": sum(1 for item in items if not item.evaluable),
        "degraded_execution_count": sum(1 for item in items if item.degraded_execution),
        "skill_tool_executed_count": sum(1 for item in items if item.skills_enabled and skill_audit(item).get("skill_tool_executed")),
        "skill_model_declared_skip_count": sum(1 for item in items if item.skills_enabled and skill_audit(item).get("model_declared_skip")),
        "skill_no_tool_call_count": sum(1 for item in items if item.skills_enabled and skill_audit(item).get("no_skill_tool_call")),
        "exec_tool_call_total": sum(exec_tool_call_count(item) for item in items),
        "exec_tool_failure_total": sum(exec_tool_failure_count(item) for item in items),
        "skill_tool_call_total": sum(skill_tool_call_count(item) for item in items),
        "skill_tool_failure_total": sum(skill_tool_failure_count(item) for item in items),
        "openclaw_tool_call_total": sum(openclaw_tool_call_count(item) for item in items),
        "openclaw_tool_failure_total": sum(openclaw_tool_failure_count(item) for item in items),
        "missing_skill_doc_read_total": sum(skill_audit_int(item, "missing_skill_doc_read_count", skill_enabled_only=True) for item in items),
        "tool_result_error_total": sum(skill_audit_int(item, "tool_result_error_count") for item in items),
        "request_shape_error_total": sum(skill_audit_int(item, "request_shape_error_count") for item in items),
        "coverage_checklist_present_count": sum(1 for item in items if skill_audit(item).get("coverage_checklist_present")),
        "session_isolation_ok_count": sum(1 for item in items if session_isolation_audit(item).get("session_isolation_ok") is True),
        "session_isolation_failed_count": sum(1 for item in items if session_isolation_failed(item)),
        "session_contaminated_count": sum(1 for item in items if session_contaminated(item)),
        "workspace_isolation_ok_count": sum(1 for item in items if workspace_isolation_ok(item)),
        "workspace_isolation_failed_count": sum(1 for item in items if workspace_isolation_failed(item)),
        "workspace_contaminated_count": sum(
            1 for item in items if workspace_isolation_audit(item).get("contaminated") is True
        ),
        "workspace_archive_failed_count": sum(
            1
            for item in items
            if workspace_isolation_audit(item)
            and workspace_isolation_audit(item).get("archive_ok") is False
        ),
        "avg_score": sum(float(item.evaluation["score"]) for item in items) / len(items),
        "avg_normalized_score": sum(float(item.evaluation["normalized_score"]) for item in items) / len(items),
        "avg_elapsed_seconds": sum(float(item.elapsed_seconds) for item in items) / len(items),
        "avg_answer_accuracy": average_optional_metric(items, "answer_accuracy"),
        "avg_rpf": average_optional_metric(items, "rpf"),
        "hle_calibration_rmse": hle_calibration_rmse(items),
    }


def aggregate_results(results: list[GroupRecordResult]) -> dict[str, Any]:
    grouped: dict[str, list[GroupRecordResult]] = {}
    for item in results:
        grouped.setdefault(item.group_id, []).append(item)

    summary_groups: dict[str, Any] = {}
    summary_group_subset: dict[str, dict[str, Any]] = {}
    for group_id, items in grouped.items():
        by_eval_kind: dict[str, list[GroupRecordResult]] = {}
        by_subset: dict[str, list[GroupRecordResult]] = {}
        for item in items:
            by_eval_kind.setdefault(item.eval_kind, []).append(item)
            by_subset.setdefault(item.subset, []).append(item)
        bucket = aggregate_bucket(items)
        summary_groups[group_id] = {
            "group_label": items[0].group_label,
            "runner": items[0].runner,
            "websearch": items[0].websearch,
            "skills_enabled": items[0].skills_enabled,
            **bucket,
            "by_eval_kind": {
                eval_kind: {
                    key: value
                    for key, value in aggregate_bucket(eval_items).items()
                }
                for eval_kind, eval_items in by_eval_kind.items()
            },
            "by_subset": {
                subset: {
                    key: value
                    for key, value in aggregate_bucket(subset_items).items()
                }
                for subset, subset_items in by_subset.items()
            },
        }
        for subset, subset_items in by_subset.items():
            summary_group_subset[f"{group_id}::{subset}"] = {
                "group_id": group_id,
                "group_label": items[0].group_label,
                "runner": items[0].runner,
                "websearch": items[0].websearch,
                "skills_enabled": items[0].skills_enabled,
                "subset": subset,
                **aggregate_bucket(subset_items),
            }

    return {
        "group_order": list(grouped.keys()),
        "groups": summary_groups,
        "group_subset": summary_group_subset,
    }


def build_error_group_record_result(
    *,
    group: Any,
    record: Any,
    error_message: str,
    elapsed_seconds: float = 0.0,
    answer_text: str = "",
    short_answer_text: str = "",
    full_response_text: str = "",
    runner_meta: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
    classify_subset_fn: Callable[[Any], str],
    normalize_answer_tracks_fn: Callable[..., tuple[str, str]],
    build_execution_error_evaluation_fn: Callable[..., Any],
    deep_copy_jsonish_fn: Callable[[Any], Any],
) -> GroupRecordResult:
    evaluation = build_execution_error_evaluation_fn(record, error_message=error_message)
    if is_dataclass(evaluation):
        evaluation_payload = asdict(evaluation)
    elif isinstance(evaluation, dict):
        evaluation_payload = deep_copy_jsonish_fn(evaluation)
    else:
        raise TypeError("build_execution_error_evaluation_fn must return a dataclass or dict payload")

    meta = deep_copy_jsonish_fn(runner_meta or {})
    meta.setdefault("error", error_message)
    payload = deep_copy_jsonish_fn(raw or {"error": error_message})
    short_text, full_text = normalize_answer_tracks_fn(
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    compatible_answer_text = answer_text or full_text or short_text
    return GroupRecordResult(
        schema_version=2,
        group_id=str(getattr(group, "id", "") or ""),
        group_label=str(getattr(group, "label", "") or ""),
        runner=str(getattr(group, "runner", "") or ""),
        websearch=bool(getattr(group, "websearch", False)),
        skills_enabled=bool(getattr(group, "skills_enabled", False)),
        record_id=str(getattr(record, "record_id", "") or ""),
        subset=classify_subset_fn(record),
        dataset=str(getattr(record, "dataset", "") or ""),
        source_file=str(getattr(record, "source_file", "") or ""),
        eval_kind=str(getattr(record, "eval_kind", "") or ""),
        prompt=str(getattr(record, "prompt", "") or ""),
        reference_answer=str(getattr(record, "reference_answer", "") or ""),
        answer_text=compatible_answer_text,
        evaluation=evaluation_payload,
        runner_meta=meta,
        raw=payload,
        elapsed_seconds=elapsed_seconds,
        run_lifecycle_status="failed",
        protocol_completion_status="missing",
        protocol_acceptance_status=None,
        answer_availability="missing",
        answer_reliability="none",
        evaluable=False,
        scored=False,
        recovery_mode="none",
        degraded_execution=False,
        execution_error_kind="execution_error",
        error=error_message,
        short_answer_text=short_text,
        full_response_text=full_text,
    )


def materialize_group_failure_results(
    *,
    group: Any,
    records: list[Any],
    output_root: Path,
    error_message: str,
    save_json_fn: Callable[[Path, Any], None],
    slugify_fn: Callable[..., str],
    classify_subset_fn: Callable[[Any], str],
    normalize_answer_tracks_fn: Callable[..., tuple[str, str]],
    build_execution_error_evaluation_fn: Callable[..., Any],
    deep_copy_jsonish_fn: Callable[[Any], Any],
) -> list[GroupRecordResult]:
    group_results = [
        build_error_group_record_result(
            group=group,
            record=record,
            error_message=error_message,
            classify_subset_fn=classify_subset_fn,
            normalize_answer_tracks_fn=normalize_answer_tracks_fn,
            build_execution_error_evaluation_fn=build_execution_error_evaluation_fn,
            deep_copy_jsonish_fn=deep_copy_jsonish_fn,
        )
        for record in records
    ]
    for entry in group_results:
        save_json_fn(output_root / "per-record" / str(getattr(group, "id", "")) / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
    return group_results
