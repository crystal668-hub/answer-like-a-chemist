from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any


def unavailable_observability(*, record_wall_seconds: float | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "coverage": {
            "timing": "exact" if record_wall_seconds is not None else "unavailable",
            "tokens": "unavailable",
            "tools": "unavailable",
            "packages": "unavailable",
            "resources": "unavailable",
        },
        "totals": {
            "timing": (
                {"record_wall_seconds": max(0.0, float(record_wall_seconds))}
                if record_wall_seconds is not None
                else {}
            ),
            "tokens": {},
            "tools": {},
            "packages": {},
            "resources": {},
        },
        "attempt_count": 0,
        "attempts": [],
        "final_attempt": None,
    }


@dataclass
class GroupRecordResult:
    schema_version: int
    group_id: str
    group_label: str
    runner: str
    websearch: bool
    record_id: str
    track: str
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
    observability: dict[str, Any] = field(default_factory=unavailable_observability)


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
        self._observation_coverage = {
            key: 0 for key in ("timing", "tokens", "tools", "packages", "resources")
        }
        self._observation_totals: dict[str, float] = {
            "record_wall_seconds": 0.0,
            "record_wall_max_seconds": 0.0,
            "input_tokens": 0.0,
            "output_tokens": 0.0,
            "cache_read_tokens": 0.0,
            "cache_write_tokens": 0.0,
            "reasoning_tokens": 0.0,
            "total_tokens": 0.0,
            "tool_calls": 0.0,
            "tool_failures": 0.0,
            "exec_calls": 0.0,
            "exec_failures": 0.0,
            "package_install_events": 0.0,
            "package_install_failures": 0.0,
            "cpu_peak_percent": 0.0,
            "memory_peak_bytes": 0.0,
            "pids_peak": 0.0,
        }
        self._observed_packages: set[str] = set()
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
        observability = item.observability if isinstance(item.observability, dict) else {}
        coverage = observability.get("coverage") if isinstance(observability.get("coverage"), dict) else {}
        totals = observability.get("totals") if isinstance(observability.get("totals"), dict) else {}
        for key in self._observation_coverage:
            self._observation_coverage[key] += int(coverage.get(key) == "exact")
        if coverage.get("timing") == "exact":
            timing = totals.get("timing") if isinstance(totals.get("timing"), dict) else {}
            wall = float(timing.get("record_wall_seconds") or 0.0)
            self._observation_totals["record_wall_seconds"] += wall
            self._observation_totals["record_wall_max_seconds"] = max(
                self._observation_totals["record_wall_max_seconds"], wall
            )
        if coverage.get("tokens") == "exact":
            tokens = totals.get("tokens") if isinstance(totals.get("tokens"), dict) else {}
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_tokens",
                "cache_write_tokens",
                "reasoning_tokens",
                "total_tokens",
            ):
                self._observation_totals[key] += float(tokens.get(key) or 0)
        if coverage.get("tools") == "exact":
            tools = totals.get("tools") if isinstance(totals.get("tools"), dict) else {}
            for source, target in (
                ("call_count", "tool_calls"),
                ("failure_count", "tool_failures"),
                ("exec_call_count", "exec_calls"),
                ("exec_failure_count", "exec_failures"),
            ):
                self._observation_totals[target] += float(tools.get(source) or 0)
        if coverage.get("packages") == "exact":
            packages = totals.get("packages") if isinstance(totals.get("packages"), dict) else {}
            self._observation_totals["package_install_events"] += float(packages.get("install_event_count") or 0)
            self._observation_totals["package_install_failures"] += float(packages.get("failed_install_event_count") or 0)
            self._observed_packages.update(str(name) for name in packages.get("added_packages", []) if str(name))
        if coverage.get("resources") == "exact":
            resources = totals.get("resources") if isinstance(totals.get("resources"), dict) else {}
            for source, target in (
                ("cpu_peak_percent", "cpu_peak_percent"),
                ("memory_peak_bytes", "memory_peak_bytes"),
                ("pids_peak", "pids_peak"),
            ):
                self._observation_totals[target] = max(
                    self._observation_totals[target], float(resources.get(source) or 0)
                )

    def to_dict(self) -> dict[str, Any]:
        result = {"count": self.count, **self._counters}
        result.update({"avg_score": self._score_sum / self._scored if self._scored else 0.0, "avg_normalized_score": self._normalized_sum / self._scored if self._scored else 0.0, "avg_elapsed_seconds": self._elapsed_sum / self.count if self.count else 0.0})
        for key, (total, count) in self._optional.items():
            result[f"avg_{key}"] = total / count if count else None
        result["hle_calibration_rmse"] = math.sqrt(self._hle_sse / self._hle_count) if self._hle_count else None
        exact_timing = self._observation_coverage["timing"]
        tool_calls = self._observation_totals["tool_calls"]
        exec_calls = self._observation_totals["exec_calls"]
        result["observability"] = {
            "coverage": {
                key: {"exact": value, "total": self.count}
                for key, value in self._observation_coverage.items()
            },
            "timing": {
                "avg_record_wall_seconds": (
                    self._observation_totals["record_wall_seconds"] / exact_timing
                    if exact_timing
                    else None
                ),
                "max_record_wall_seconds": self._observation_totals["record_wall_max_seconds"] if exact_timing else None,
            },
            "tokens": {
                key: int(self._observation_totals[key])
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                )
            },
            "tools": {
                "call_count": int(tool_calls),
                "failure_count": int(self._observation_totals["tool_failures"]),
                "failure_rate": self._observation_totals["tool_failures"] / tool_calls if tool_calls else 0.0,
                "exec_call_count": int(exec_calls),
                "exec_failure_count": int(self._observation_totals["exec_failures"]),
                "exec_failure_rate": self._observation_totals["exec_failures"] / exec_calls if exec_calls else 0.0,
            },
            "packages": {
                "install_event_count": int(self._observation_totals["package_install_events"]),
                "failed_install_event_count": int(self._observation_totals["package_install_failures"]),
                "unique_added_package_count": len(self._observed_packages),
                "added_packages": sorted(self._observed_packages),
            },
            "resources": {
                "cpu_peak_percent": self._observation_totals["cpu_peak_percent"],
                "memory_peak_bytes": int(self._observation_totals["memory_peak_bytes"]),
                "pids_peak": int(self._observation_totals["pids_peak"]),
            },
        }
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


def aggregate_bucket(items: Iterable[GroupRecordResult]) -> dict[str, Any]:
    """Return a reporting bucket using bounded incremental state."""
    accumulator = AggregateAccumulator()
    for item in items:
        accumulator.add(item)
    return accumulator.to_dict()

def aggregate_results(results: Iterable[GroupRecordResult]) -> dict[str, Any]:
    """Aggregate records in one pass without retaining record payloads."""
    groups: dict[str, dict[str, Any]] = {}
    group_order: list[str] = []
    for item in results:
        group = groups.get(item.group_id)
        if group is None:
            group = {
                "meta": {
                    "group_label": item.group_label,
                    "runner": item.runner,
                    "websearch": item.websearch,
                    "skills_enabled": item.skills_enabled,
                },
                "bucket": AggregateAccumulator(),
                "eval": {},
                "track": {},
            }
            groups[item.group_id] = group
            group_order.append(item.group_id)
        group["bucket"].add(item)
        eval_acc = group["eval"].setdefault(item.eval_kind, AggregateAccumulator())
        eval_acc.add(item)
        track_acc = group["track"].setdefault(item.track, AggregateAccumulator())
        track_acc.add(item)

    summary_groups: dict[str, Any] = {}
    summary_group_track: dict[str, dict[str, Any]] = {}
    for group_id in group_order:
        group = groups[group_id]
        meta = group["meta"]
        summary_groups[group_id] = {
            **meta,
            **group["bucket"].to_dict(),
            "by_eval_kind": {key: value.to_dict() for key, value in group["eval"].items()},
            "by_track": {key: value.to_dict() for key, value in group["track"].items()},
        }
        for track, accumulator in group["track"].items():
            summary_group_track[f"{group_id}::{track}"] = {
                "group_id": group_id,
                **meta,
                "track": track,
                **accumulator.to_dict(),
            }

    return {
        "group_order": group_order,
        "groups": summary_groups,
        "group_track": summary_group_track,
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
        schema_version=5,
        group_id=str(getattr(group, "id", "") or ""),
        group_label=str(getattr(group, "label", "") or ""),
        runner=str(getattr(group, "runner", "") or ""),
        websearch=bool(getattr(group, "websearch", False)),
        skills_enabled=bool(getattr(group, "skills_enabled", False)),
        record_id=str(getattr(record, "record_id", "") or ""),
        track=str(getattr(record, "track", "") or ""),
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
        observability=(
            meta.get("observability")
            if isinstance(meta.get("observability"), dict) and meta.get("observability")
            else unavailable_observability(record_wall_seconds=elapsed_seconds)
        ),
    )


def materialize_group_failure_results(
    *,
    group: Any,
    records: list[Any],
    output_root: Path,
    error_message: str,
    save_json_fn: Callable[[Path, Any], None],
    slugify_fn: Callable[..., str],
    normalize_answer_tracks_fn: Callable[..., tuple[str, str]],
    build_execution_error_evaluation_fn: Callable[..., Any],
    deep_copy_jsonish_fn: Callable[[Any], Any],
    result_reference_fn: Callable[[GroupRecordResult, Path], Any] | None = None,
) -> list[Any]:
    group_results: list[Any] = []
    for record in records:
        entry = build_error_group_record_result(
            group=group,
            record=record,
            error_message=error_message,
            normalize_answer_tracks_fn=normalize_answer_tracks_fn,
            build_execution_error_evaluation_fn=build_execution_error_evaluation_fn,
            deep_copy_jsonish_fn=deep_copy_jsonish_fn,
        )
        path = output_root / "per-record" / str(getattr(group, "id", "")) / f"{slugify_fn(entry.record_id)}.json"
        save_json_fn(path, asdict(entry))
        group_results.append(result_reference_fn(entry, path) if result_reference_fn is not None else entry)
    return group_results
