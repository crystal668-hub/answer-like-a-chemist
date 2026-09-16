from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any


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


class AggregateAccumulator:
    """Incrementally accumulate the reporting bucket contract.

    The accumulator keeps counters and numeric totals only; record payloads are
    never retained.  ``to_dict`` emits the same keys as ``aggregate_bucket``.
    """

    def __init__(self) -> None:
        self.count = 0
        self._scored = 0
        self._score_sum = 0.0
        self._normalized_sum = 0.0
        self._elapsed_sum = 0.0
        self._optional: dict[str, tuple[float, int]] = {"answer_accuracy": (0.0, 0), "rpf": (0.0, 0)}
        self._hle_sse = 0.0
        self._hle_count = 0
        self._counters = {
            key: 0
            for key in (
                "pass_count", "run_completed_count", "run_failed_count",
                "protocol_completed_count", "protocol_failed_count", "evaluable_count",
                "scored_count", "recovered_evaluable_count", "native_evaluable_count",
                "non_evaluable_count", "degraded_execution_count", "skill_tool_executed_count",
                "skill_model_declared_skip_count", "skill_no_tool_call_count",
                "exec_tool_call_total", "exec_tool_failure_total", "skill_tool_call_total",
                "skill_tool_failure_total", "openclaw_tool_call_total",
                "openclaw_tool_failure_total", "missing_skill_doc_read_total",
                "tool_result_error_total", "request_shape_error_total",
                "coverage_checklist_present_count", "session_isolation_ok_count",
                "session_isolation_failed_count", "session_contaminated_count",
                "workspace_isolation_ok_count", "workspace_isolation_failed_count",
                "workspace_contaminated_count", "boundary_warning_count",
                "boundary_violation_count", "scoreable_degraded_boundary_count",
                "information_contamination_count", "contamination_indeterminate_count",
                "audit_unavailable_count", "boundary_cleanup_failed_count",
                "workspace_archive_failed_count",
            )
        }

    def add(self, item: GroupRecordResult) -> None:
        self.count += 1
        self._elapsed_sum += float(item.elapsed_seconds)
        evaluation = item.evaluation or {}
        skill = skill_audit(item)
        session = session_isolation_audit(item)
        workspace = workspace_isolation_audit(item)
        workspace_ok = bool(
            workspace
            and workspace.get("preflight_ok") is True
            and workspace.get("audit_execution_status") == "complete"
            and workspace.get("adjudication") in {"scoreable", "scoreable_degraded"}
            and workspace.get("archive_ok") is True
        )
        session_failed = session.get("session_isolation_ok") is False
        session_contamination = session_failed and bool(
            str(session.get("postflight_entry_session_id") or "").strip()
            and str(session.get("postflight_entry_session_id") or "").strip()
            != str(session.get("requested_session_id") or "").strip()
        )
        counters = self._counters
        counters["pass_count"] += int(bool(evaluation.get("passed")))
        counters["run_completed_count"] += int(item.run_lifecycle_status == "completed")
        counters["run_failed_count"] += int(item.run_lifecycle_status == "failed")
        counters["protocol_completed_count"] += int(item.protocol_completion_status == "completed")
        counters["protocol_failed_count"] += int(item.protocol_completion_status == "failed")
        counters["evaluable_count"] += int(item.evaluable)
        counters["scored_count"] += int(item.scored)
        counters["recovered_evaluable_count"] += int(item.evaluable and item.recovery_mode != "none")
        counters["native_evaluable_count"] += int(item.evaluable and item.recovery_mode == "none")
        counters["non_evaluable_count"] += int(not item.evaluable)
        counters["degraded_execution_count"] += int(item.degraded_execution)
        counters["skill_tool_executed_count"] += int(item.skills_enabled and bool(skill.get("skill_tool_executed")))
        counters["skill_model_declared_skip_count"] += int(item.skills_enabled and bool(skill.get("model_declared_skip")))
        counters["skill_no_tool_call_count"] += int(item.skills_enabled and bool(skill.get("no_skill_tool_call")))
        counters["coverage_checklist_present_count"] += int(bool(skill.get("coverage_checklist_present")))
        counters["session_isolation_ok_count"] += int(session.get("session_isolation_ok") is True)
        counters["session_isolation_failed_count"] += int(session_failed)
        counters["session_contaminated_count"] += int(session_contamination)
        counters["workspace_isolation_ok_count"] += int(workspace_ok)
        counters["workspace_isolation_failed_count"] += int(bool(workspace) and not workspace_ok)
        contamination = workspace.get("contamination_status")
        counters["workspace_contaminated_count"] += int(contamination == "confirmed")
        counters["information_contamination_count"] += int(contamination == "confirmed")
        counters["contamination_indeterminate_count"] += int(contamination == "indeterminate")
        counters["boundary_warning_count"] += int(workspace.get("boundary_status") == "warning")
        counters["boundary_violation_count"] += int(workspace.get("boundary_status") == "violated")
        counters["scoreable_degraded_boundary_count"] += int(workspace.get("adjudication") == "scoreable_degraded")
        counters["audit_unavailable_count"] += int(workspace.get("audit_execution_status") == "unavailable")
        counters["boundary_cleanup_failed_count"] += int(bool((workspace.get("cleanup") or {}).get("failed_count", 0)))
        counters["workspace_archive_failed_count"] += int(bool(workspace) and workspace.get("archive_ok") is False)

        def audit_int(key: str) -> int:
            value = skill.get(key)
            return int(value) if isinstance(value, (int, float)) else 0

        exec_calls = skill.get("exec_tool_call_count")
        exec_failures = skill.get("exec_tool_failure_count")
        if not isinstance(exec_calls, (int, float)) and not item.skills_enabled:
            exec_calls = skill.get("skill_tool_call_count")
        if not isinstance(exec_failures, (int, float)) and not item.skills_enabled:
            exec_failures = skill.get("skill_tool_failure_count")
        openclaw_calls = skill.get("openclaw_tool_call_count")
        openclaw_failures = skill.get("openclaw_tool_failure_count")
        if not isinstance(openclaw_calls, (int, float)):
            openclaw_calls = skill.get("tool_call_count")
        if not isinstance(openclaw_failures, (int, float)):
            openclaw_failures = skill.get("tool_failure_count")
        counters["exec_tool_call_total"] += int(exec_calls) if isinstance(exec_calls, (int, float)) else 0
        counters["exec_tool_failure_total"] += int(exec_failures) if isinstance(exec_failures, (int, float)) else 0
        counters["skill_tool_call_total"] += audit_int("skill_tool_call_count") if item.skills_enabled else 0
        counters["skill_tool_failure_total"] += audit_int("skill_tool_failure_count") if item.skills_enabled else 0
        counters["openclaw_tool_call_total"] += int(openclaw_calls) if isinstance(openclaw_calls, (int, float)) else 0
        counters["openclaw_tool_failure_total"] += int(openclaw_failures) if isinstance(openclaw_failures, (int, float)) else 0
        counters["missing_skill_doc_read_total"] += audit_int("missing_skill_doc_read_count") if item.skills_enabled else 0
        counters["tool_result_error_total"] += audit_int("tool_result_error_count")
        counters["request_shape_error_total"] += audit_int("request_shape_error_count")
        if item.scored:
            self._scored += 1
            self._score_sum += float(evaluation.get("score") or 0.0)
            self._normalized_sum += float(evaluation.get("normalized_score") or 0.0)
        details = evaluation.get("details") or {}
        for key in self._optional:
            value = details.get(key)
            if isinstance(value, (int, float)):
                total, count = self._optional[key]
                self._optional[key] = (total + float(value), count + 1)
        if item.eval_kind == "hle" and isinstance(details.get("confidence"), (int, float)):
            confidence = max(0.0, min(100.0, float(details["confidence"]))) / 100.0
            self._hle_sse += (confidence - (1.0 if evaluation.get("passed") else 0.0)) ** 2
            self._hle_count += 1

    def to_dict(self) -> dict[str, Any]:
        result = {"count": self.count, **self._counters}
        result.update({"avg_score": self._score_sum / self._scored if self._scored else 0.0, "avg_normalized_score": self._normalized_sum / self._scored if self._scored else 0.0, "avg_elapsed_seconds": self._elapsed_sum / self.count if self.count else 0.0})
        for key, (total, count) in self._optional.items():
            result[f"avg_{key}"] = total / count if count else None
        result["hle_calibration_rmse"] = math.sqrt(self._hle_sse / self._hle_count) if self._hle_count else None
        return result


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
    if not isinstance(audit, dict):
        return {}
    if "adjudication" in audit:
        return audit
    legacy_status = str(audit.get("audit_status") or "").strip()
    if not legacy_status:
        return audit
    adapted = dict(audit)
    adapted.update(
        legacy_schema=True,
        audit_execution_status="unavailable" if legacy_status == "unavailable" else "complete",
        boundary_status="clean" if legacy_status == "clean" else "unknown",
        contamination_status="clear" if legacy_status == "clean" else "indeterminate",
        adjudication="scoreable" if legacy_status == "clean" else "non_evaluable",
    )
    return adapted


def workspace_isolation_ok(item: GroupRecordResult) -> bool:
    audit = workspace_isolation_audit(item)
    return bool(
        audit
        and audit.get("preflight_ok") is True
        and audit.get("audit_execution_status") == "complete"
        and audit.get("adjudication") in {"scoreable", "scoreable_degraded"}
        and audit.get("archive_ok") is True
    )


def workspace_isolation_failed(item: GroupRecordResult) -> bool:
    audit = workspace_isolation_audit(item)
    return bool(audit and not workspace_isolation_ok(item))


def aggregate_bucket(items: list[GroupRecordResult]) -> dict[str, Any]:
    """Return a reporting bucket using bounded incremental state."""
    accumulator = AggregateAccumulator()
    for item in items:
        accumulator.add(item)
    return accumulator.to_dict()

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
        schema_version=3,
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
