from __future__ import annotations

import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .convergence import ConvergencePolicy
from .reporting import GroupRecordResult
from .status import build_result_axes_from_runner


class OrchestrationError(RuntimeError):
    pass


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


def run_group(
    *,
    group: Any,
    records: list[Any],
    output_root: Path,
    single_timeout: int,
    chemqa_timeout: int,
    judge: Any,
    config_path: Path,
    single_agent: str,
    chemqa_root: Path,
    chemqa_model_profile: str,
    review_rounds: int | None,
    rebuttal_rounds: int | None,
    chemqa_slot_sets: dict[str, str],
    experiment_specs: dict[str, Any],
    build_runner_fn: Callable[..., Any],
    evaluate_answer_fn: Callable[..., Any],
    build_error_group_record_result_fn: Callable[..., GroupRecordResult],
    classify_subset_fn: Callable[[Any], str],
    save_json_fn: Callable[[Path, Any], None],
    slugify_fn: Callable[..., str],
    single_convergence_policy: ConvergencePolicy | None = None,
    chemqa_convergence_policy: ConvergencePolicy | None = None,
    skill_health_summary: dict[str, Any] | None = None,
) -> list[GroupRecordResult]:
    runtime_bundle_root = output_root / "input-bundles"
    try:
        if group.runner == "chemqa":
            runner = build_runner_fn(
                runner_kind=group.runner,
                chemqa_root=chemqa_root,
                timeout_seconds=chemqa_timeout,
                config_path=config_path,
                slot_set=chemqa_slot_sets[group.id],
                review_rounds=review_rounds,
                rebuttal_rounds=rebuttal_rounds,
                model_profile=chemqa_model_profile,
                runtime_bundle_root=runtime_bundle_root,
                launch_workspace_root=output_root / "chemqa-launch",
                convergence_policy=chemqa_convergence_policy or ConvergencePolicy(timeout_seconds=chemqa_timeout),
            )
        else:
            runner = build_runner_fn(
                runner_kind=group.runner,
                agent_id=single_agent,
                timeout_seconds=single_timeout,
                config_path=config_path,
                runtime_bundle_root=runtime_bundle_root,
                configured_skills=tuple(experiment_specs[group.id].skill_allowlist or ()),
                skill_health_summary=skill_health_summary,
                convergence_policy=single_convergence_policy or ConvergencePolicy(timeout_seconds=single_timeout),
            )
    except Exception as exc:
        error_message = f"Failed to initialize runner for group `{group.id}`: {exc}"
        group_results = [
            build_error_group_record_result_fn(
                group=group,
                record=record,
                error_message=error_message,
            )
            for record in records
        ]
        for entry in group_results:
            save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(entry.record_id)}.json", asdict(entry))
        return group_results

    group_results: list[GroupRecordResult] = []
    for record in records:
        started = time.time()
        run_result: Any | None = None
        try:
            run_result = runner.run(record, group)
            ensure_compatible_runner_result(run_result, runner_kind=group.runner)
            axes = build_result_axes_from_runner(run_result)
            if run_result.should_score():
                answer_text = run_result.answer.full_response_text or run_result.answer.short_answer_text
                evaluation = evaluate_answer_fn(
                    record,
                    short_answer_text=run_result.answer.short_answer_text,
                    full_response_text=run_result.answer.full_response_text,
                    answer_text=answer_text,
                    judge=judge,
                )
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
            if run_result is not None:
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
        group_results.append(entry)
        save_json_fn(output_root / "per-record" / group.id / f"{slugify_fn(record.record_id)}.json", asdict(entry))
    return group_results
