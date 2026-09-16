from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from benchmarking.core.reporting import GroupRecordResult
from benchmarking.core.status import build_result_axes_from_runner
from benchmarking.runtime.cancellation import BenchmarkCancelledError, CancellationToken
from benchmarking.workflow.attempt_queue import WorkStep, staged, persist_runner_result, load_runner_result
from benchmarking.core.contracts import RunnerResult


class OrchestrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PersistedResultRef:
    """Small runtime handle used after a result has been written to disk."""

    group_id: str
    record_id: str
    run_lifecycle_status: str
    error: str | None = None
    archive_error: str | None = None
    cleanup_failed_count: int = 0
    score: float | None = None
    path: str | None = None


def build_cancelled_group_record_result(
    *,
    group: Any,
    record: Any,
    build_error_group_record_result_fn: Callable[..., GroupRecordResult],
    elapsed_seconds: float = 0.0,
    runner_meta: dict[str, Any] | None = None,
    raw: dict[str, Any] | None = None,
) -> GroupRecordResult:
    message = "Benchmark run cancelled before evaluation completed."
    entry = build_error_group_record_result_fn(
        group=group,
        record=record,
        error_message=message,
        elapsed_seconds=elapsed_seconds,
        runner_meta={**dict(runner_meta or {}), "cancellation": {"status": "cancelled"}},
        raw=dict(raw or {"status": "cancelled"}),
    )
    payload = asdict(entry)
    payload.update(
        {
            "run_lifecycle_status": "cancelled",
            "protocol_completion_status": "missing",
            "answer_availability": "missing",
            "answer_reliability": "none",
            "evaluable": False,
            "scored": False,
            "recovery_mode": "none",
            "degraded_execution": False,
            "execution_error_kind": "cancelled",
            "evaluation": {
                "eval_kind": str(getattr(record, "eval_kind", "") or ""),
                "score": None,
                "max_score": None,
                "normalized_score": None,
                "passed": None,
                "primary_metric": "cancelled",
                "primary_metric_direction": "not_applicable",
                "details": {"execution_error_kind": "cancelled"},
            },
            "error": message,
        }
    )
    return GroupRecordResult(**payload)


def ensure_compatible_runner_result(run_result: Any, *, runner_kind: str) -> None:
    missing: list[str] = []
    should_score = getattr(run_result, "should_score", None)
    if not callable(should_score):
        missing.append("callable should_score()")
    answer = getattr(run_result, "answer", None)
    if answer is None:
        missing.append("answer")
    else:
        if not hasattr(answer, "short_answer_text"):
            missing.append("answer.short_answer_text")
        if not hasattr(answer, "full_response_text"):
            missing.append("answer.full_response_text")
    if not isinstance(getattr(run_result, "runner_meta", None), dict):
        missing.append("runner_meta: dict")
    if not isinstance(getattr(run_result, "raw", None), dict):
        missing.append("raw: dict")
    if not hasattr(run_result, "status"):
        missing.append("status")
    failure = getattr(run_result, "failure", None)
    if failure is not None and not hasattr(failure, "message"):
        missing.append("failure.message")
    if missing:
        raise OrchestrationError(
            f"Runner `{runner_kind}` returned incompatible result object `{type(run_result).__name__}`; "
            f"missing/invalid fields: {', '.join(missing)}"
        )


def status_label(run_result: Any) -> str:
    status = getattr(run_result, "status", None)
    if status is None:
        return "unknown"
    return str(getattr(status, "value", status))


@staged
def run_group(
    *,
    group: Any,
    records: list[Any],
    output_root: Path,
    judge: Any,
    runner_options_factory: Callable[[], dict[str, Any]],
    build_runner_fn: Callable[..., Any],
    evaluate_answer_fn: Callable[..., Any],
    build_error_group_record_result_fn: Callable[..., GroupRecordResult],
    classify_subset_fn: Callable[[Any], str],
    save_json_fn: Callable[[Path, Any], None],
    slugify_fn: Callable[..., str],
    progress_writer: Any | None = None,
    cancellation_token: CancellationToken | None = None,
    manage_group_lifecycle: bool = True,
    retain_results: bool = True,
) -> list[GroupRecordResult]:
    def mark_cancelling() -> None:
        if progress_writer is None or cancellation_token is None:
            return
        reason = cancellation_token.reason
        progress_writer.run_cancelling(reason=reason.to_payload() if reason is not None else {})

    def persisted_ref(entry: GroupRecordResult) -> PersistedResultRef:
        evaluation = entry.evaluation if isinstance(entry.evaluation, dict) else {}
        score = evaluation.get("normalized_score", evaluation.get("score"))
        isolation = (entry.runner_meta or {}).get("workspace_isolation") or {}
        cleanup = isolation.get("cleanup") if isinstance(isolation, dict) else {}
        return PersistedResultRef(
            group.id, entry.record_id, entry.run_lifecycle_status, entry.error,
            archive_error=(isolation.get("archive_error") if isinstance(isolation, dict) else None),
            cleanup_failed_count=int((cleanup or {}).get("failed_count") or 0) if isinstance(cleanup, dict) else 0,
            score=float(score) if isinstance(score, (int, float)) else None,
            path=str(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json"),
        )

    if cancellation_token is not None and cancellation_token.is_cancelled:
        mark_cancelling()
        if not retain_results:
            refs = []
            for record in records:
                entry = build_cancelled_group_record_result(group=group, record=record, build_error_group_record_result_fn=build_error_group_record_result_fn)
                save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
                refs.append(persisted_ref(entry))
                if progress_writer is not None:
                    progress_writer.record_cancelled(group.id, entry.record_id)
            if progress_writer is not None:
                progress_writer.group_cancelled(group.id)
            return refs
        group_results = [
            build_cancelled_group_record_result(
                group=group,
                record=record,
                build_error_group_record_result_fn=build_error_group_record_result_fn,
            )
            for record in records
        ]
        refs = []
        for entry in group_results:
            save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
            if not retain_results:
                refs.append(persisted_ref(entry))
            if progress_writer is not None:
                progress_writer.record_cancelled(group.id, entry.record_id)
        if progress_writer is not None:
            progress_writer.group_cancelled(group.id)
        return refs if not retain_results else group_results
    try:
        runner = build_runner_fn(runner_kind=group.runner, **runner_options_factory())
    except Exception as exc:
        if cancellation_token is not None and cancellation_token.is_cancelled:
            mark_cancelling()
            if not retain_results:
                refs = []
                for record in records:
                    entry = build_cancelled_group_record_result(group=group, record=record, build_error_group_record_result_fn=build_error_group_record_result_fn)
                    save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
                    refs.append(persisted_ref(entry))
                    if progress_writer is not None:
                        progress_writer.record_cancelled(group.id, entry.record_id)
                if progress_writer is not None:
                    progress_writer.group_cancelled(group.id)
                return refs
            group_results = [
                build_cancelled_group_record_result(
                    group=group,
                    record=record,
                    build_error_group_record_result_fn=build_error_group_record_result_fn,
                )
                for record in records
            ]
            refs = []
            for entry in group_results:
                save_json_fn(
                    output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json",
                    asdict(entry),
                )
                if not retain_results:
                    refs.append(persisted_ref(entry))
                if progress_writer is not None:
                    progress_writer.record_cancelled(group.id, entry.record_id)
            if progress_writer is not None:
                progress_writer.group_cancelled(group.id)
            return refs if not retain_results else group_results
        error_message = f"Failed to initialize runner for group `{group.id}`: {exc}"
        if progress_writer is not None and manage_group_lifecycle:
            progress_writer.group_started(group.id)
            progress_writer.error(group_id=group.id, message=error_message)
        if not retain_results:
            refs = []
            for index, record in enumerate(records, start=1):
                entry = build_error_group_record_result_fn(group=group, record=record, error_message=error_message)
                save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
                refs.append(persisted_ref(entry))
                if progress_writer is not None:
                    progress_writer.record_started(group.id, str(entry.record_id), index=index)
                    progress_writer.record_completed(group.id, str(entry.record_id), status="failed", score=0.0)
            if progress_writer is not None and manage_group_lifecycle:
                progress_writer.group_completed(group.id, status="failed")
            return refs
        group_results = [
            build_error_group_record_result_fn(
                group=group,
                record=record,
                error_message=error_message,
            )
            for record in records
        ]
        refs = []
        for index, entry in enumerate(group_results, start=1):
            save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
            if not retain_results:
                refs.append(persisted_ref(entry))
            if progress_writer is not None:
                progress_writer.record_started(group.id, str(entry.record_id), index=index)
                progress_writer.record_completed(group.id, str(entry.record_id), status="failed", score=0.0)
        if progress_writer is not None and manage_group_lifecycle:
            progress_writer.group_completed(group.id, status="failed")
        return refs if not retain_results else group_results

    group_results: list[Any] = []
    if progress_writer is not None and manage_group_lifecycle:
        progress_writer.group_started(group.id)
    for index, record in enumerate(records, start=1):
        if cancellation_token is not None and cancellation_token.is_cancelled:
            mark_cancelling()
            entry = build_cancelled_group_record_result(
                group=group,
                record=record,
                build_error_group_record_result_fn=build_error_group_record_result_fn,
            )
            save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(record.record_id)}.json", asdict(entry))
            group_results.append(entry if retain_results else persisted_ref(entry))
            if progress_writer is not None:
                progress_writer.record_cancelled(group.id, record.record_id)
            continue
        if progress_writer is not None:
            progress_writer.record_started(group.id, record.record_id, index=index)
        started = time.time()
        run_result: Any | None = None
        try:
            if getattr(runner.run, "supports_steps", False):
                run_result = yield from runner.run(record, group, staged=True)
            else:
                run_result = yield WorkStep(lambda: runner.run(record, group))
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            ensure_compatible_runner_result(run_result, runner_kind=group.runner)
            axes = build_result_axes_from_runner(run_result)
            if run_result.should_score():
                answer_text = run_result.answer.full_response_text or run_result.answer.short_answer_text
                if isinstance(run_result, RunnerResult):
                    result_path = persist_runner_result(output_root / "scoring-pending" / group.id /
                                                       f"{slugify_fn(record.record_id)}.json", run_result)
                    run_result = None
                    def score_saved(path=result_path, selected_record=record):
                        saved = load_runner_result(path)
                        return evaluate_answer_fn(selected_record, short_answer_text=saved.answer.short_answer_text,
                            full_response_text=saved.answer.full_response_text,
                            answer_text=saved.answer.full_response_text or saved.answer.short_answer_text, judge=judge)
                    try:
                        evaluation = yield WorkStep(score_saved, kind="score")
                    finally:
                        run_result = load_runner_result(result_path)
                else:
                    evaluation = yield WorkStep(lambda: evaluate_answer_fn(
                        record, short_answer_text=run_result.answer.short_answer_text,
                        full_response_text=run_result.answer.full_response_text,
                        answer_text=answer_text, judge=judge), kind="score")
                entry = GroupRecordResult(
                    **axes,
                    group_id=group.id,
                    group_label=group.label,
                    runner=group.runner,
                    websearch=group.websearch,
                    skills_enabled=group.skills_enabled,
                    record_id=record.record_id,
                    subset=classify_subset_fn(record),
                    dataset=record.dataset,
                    source_file=record.source_file,
                    eval_kind=record.eval_kind,
                    prompt=record.prompt,
                    reference_answer=record.reference_answer,
                    answer_text=answer_text,
                    evaluation=asdict(evaluation),
                    runner_meta=run_result.runner_meta,
                    raw=run_result.raw,
                    elapsed_seconds=time.time() - started,
                    error=None,
                    short_answer_text=run_result.answer.short_answer_text,
                    full_response_text=run_result.answer.full_response_text,
                )
            else:
                runner_meta_error = str((run_result.runner_meta or {}).get("error") or "").strip()
                failure = getattr(run_result, "failure", None)
                failure_message = str(getattr(failure, "message", "") or "").strip()
                error_message = (
                    runner_meta_error
                    or failure_message
                    or f"Record `{record.record_id}` finished in non-success terminal status `{status_label(run_result)}`"
                )
                entry = build_error_group_record_result_fn(
                    group=group,
                    record=record,
                    error_message=error_message,
                    elapsed_seconds=time.time() - started,
                    short_answer_text=run_result.answer.short_answer_text,
                    full_response_text=run_result.answer.full_response_text,
                    runner_meta=run_result.runner_meta,
                    raw=run_result.raw,
                )
                entry = GroupRecordResult(**{**asdict(entry), **axes, "error": error_message})
        except Exception as exc:
            elapsed = time.time() - started
            if isinstance(exc, BenchmarkCancelledError) or (
                cancellation_token is not None and cancellation_token.is_cancelled
            ):
                mark_cancelling()
                entry = build_cancelled_group_record_result(
                    group=group,
                    record=record,
                    build_error_group_record_result_fn=build_error_group_record_result_fn,
                    elapsed_seconds=elapsed,
                    runner_meta=(dict(run_result.runner_meta) if run_result is not None else None),
                    raw=(dict(run_result.raw) if run_result is not None else None),
                )
            elif run_result is not None:
                error_message = (
                    f"Record `{record.record_id}` judge/evaluator failed in group `{group.id}` after runner output: {exc}"
                )
                runner_meta = dict(getattr(run_result, "runner_meta", {}) or {})
                runner_meta["evaluation_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "stage": "judge_or_evaluator",
                }
                runner_meta["traceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                answer = getattr(run_result, "answer", None)
                entry = build_error_group_record_result_fn(
                    group=group,
                    record=record,
                    error_message=error_message,
                    elapsed_seconds=elapsed,
                    short_answer_text=str(getattr(answer, "short_answer_text", "") or ""),
                    full_response_text=str(getattr(answer, "full_response_text", "") or ""),
                    runner_meta=runner_meta,
                    raw=getattr(run_result, "raw", {}) if isinstance(getattr(run_result, "raw", None), dict) else {},
                )
            else:
                error_message = f"Record `{record.record_id}` runner failed in group `{group.id}`: {exc}"
                entry = build_error_group_record_result_fn(
                    group=group,
                    record=record,
                    error_message=error_message,
                    elapsed_seconds=elapsed,
                    runner_meta={
                        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    },
                )
            if progress_writer is not None:
                progress_writer.error(group_id=group.id, record_id=record.record_id, message=str(exc))
        save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(record.record_id)}.json", asdict(entry))
        entry_status = entry.run_lifecycle_status
        entry_evaluation = entry.evaluation if isinstance(entry.evaluation, dict) else {}
        group_results.append(entry if retain_results else persisted_ref(entry))
        # The canonical per-record file is now the durable source of detail.
        # Drop runner output and the full entry immediately in bounded mode.
        run_result = None
        if not retain_results:
            del entry
        if progress_writer is not None:
            if entry_status == "cancelled":
                progress_writer.record_cancelled(group.id, record.record_id)
            else:
                score = entry_evaluation.get("normalized_score", entry_evaluation.get("score"))
                progress_writer.record_completed(
                    group.id,
                    record.record_id,
                    status=str(entry_status or "completed"),
                    score=float(score) if isinstance(score, (int, float)) else None,
                )
    if progress_writer is not None and manage_group_lifecycle:
        if any(item.run_lifecycle_status == "cancelled" for item in group_results):
            progress_writer.group_cancelled(group.id)
        else:
            group_status = "completed" if all(item.run_lifecycle_status == "completed" for item in group_results) else "completed_with_errors"
            progress_writer.group_completed(group.id, status=group_status)
    return group_results
