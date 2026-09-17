from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmarking.core.records import BenchmarkRecord
from benchmarking.runtime.observability import observed_duration
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.evaluators.verifier_grounded import evaluate_verifier_grounded

Evaluator = Callable[..., Any]
EVALUATORS: dict[str, Evaluator] = {}

DEFAULT_EVALUATORS: dict[str, Evaluator] = {
    "verifier_grounded": evaluate_verifier_grounded,
}


def register_evaluator(kind: str, evaluator: Evaluator) -> None:
    EVALUATORS[kind] = evaluator


def register_default_evaluators() -> None:
    EVALUATORS.update(DEFAULT_EVALUATORS)


@observed_duration("score")
def evaluate_record(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: object,
    evaluator_overrides: dict[str, Evaluator] | None = None,
    evaluators: dict[str, Evaluator] | None = None,
) -> Any:
    registry = EVALUATORS if evaluators is None else evaluators
    evaluator = (evaluator_overrides or {}).get(record.grading.kind) or registry.get(
        record.grading.kind
    )
    if evaluator is None:
        raise EvaluationRegistryError(f"No evaluator registered for '{record.grading.kind}'.")
    return evaluator(
        record,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
        answer_text=answer_text,
        judge=judge,
    )
