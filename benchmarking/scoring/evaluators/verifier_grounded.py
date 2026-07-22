from __future__ import annotations

import math
from typing import Any

from benchmarking.core.answer_processing import resolve_candidate_answer_text
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.errors import EvaluationError
from benchmarking.scoring.results import EvaluationResult
from benchmarking.runtime.vgb_bridge import VerifierGroundedRuntimeError, evaluate_answer


def _verifier_grounded_config(record: BenchmarkRecord) -> dict[str, Any]:
    payload = dict(getattr(record, "payload", {}) or {})
    config = dict(getattr(getattr(record, "grading", None), "config", {}) or {})
    value = payload.get("verifier_grounded") or config.get("verifier_grounded")
    if not isinstance(value, dict):
        raise EvaluationError("verifier_grounded records must include a verifier_grounded config object.")
    return value


def run_verifier_grounded_evaluation(*, record: BenchmarkRecord, answer_text: str) -> dict[str, Any]:
    config = _verifier_grounded_config(record)
    release = config.get("release")
    track = str(config.get("track") or "").strip()
    task_id = str(config.get("task_id") or "").strip()
    if not isinstance(release, dict) or not track or not task_id:
        raise EvaluationError("verifier_grounded config must include release, track, and task_id fields.")
    if task_id != record.record_id:
        raise EvaluationError(
            f"verifier_grounded task_id {task_id!r} does not match record_id {record.record_id!r}."
        )
    try:
        return evaluate_answer(
            track=track,
            task_id=task_id,
            answer_text=answer_text,
            release_identity=release,
        )
    except VerifierGroundedRuntimeError as exc:
        raise EvaluationError(str(exc)) from exc


def evaluate_verifier_grounded(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: Any,
    verifier_runner: Any = run_verifier_grounded_evaluation,
) -> EvaluationResult:
    candidate_answer_text = resolve_candidate_answer_text(
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    verifier_result = verifier_runner(record=record, answer_text=candidate_answer_text)
    if not isinstance(verifier_result, dict):
        raise EvaluationError("Pinned verifier returned a non-object result.")
    status = verifier_result.get("status")
    if status != "scored":
        failure_type = verifier_result.get("failure_type") or "verifier_evaluation_error"
        message = verifier_result.get("message") or "Pinned verifier did not produce a task score."
        raise EvaluationError(f"Pinned verifier failed ({failure_type}): {message}")
    scores = verifier_result.get("scores")
    raw_score = scores.get("score") if isinstance(scores, dict) else None
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        raise EvaluationError("Pinned verifier returned a scored result without a numeric score.")
    if not math.isfinite(score):
        raise EvaluationError("Pinned verifier returned a non-finite score.")
    score = max(0.0, min(1.0, score))
    details = {
        "method": "isolated_wheel_api",
        "task_id": verifier_result.get("task_id"),
        "status": status,
        "failure_type": verifier_result.get("failure_type"),
        "message": verifier_result.get("message"),
        "canonical_smiles": verifier_result.get("canonical_smiles"),
        "properties": verifier_result.get("properties") or {},
        "constraint_scores": scores.get("constraint_scores", []) if isinstance(scores, dict) else [],
        "versions": verifier_result.get("versions") or {},
        "raw_answer": verifier_result.get("raw_answer"),
        "extracted_answer": verifier_result.get("extracted_answer"),
        "verifier_result": verifier_result,
    }
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=score,
        max_score=1.0,
        normalized_score=score,
        passed=None,
        primary_metric="verifier_score",
        primary_metric_direction="higher_is_better",
        details=details,
    )
