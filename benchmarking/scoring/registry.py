from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmarking.core.datasets import BenchmarkRecord, is_retired_benchmark
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.evaluators.verifier_grounded import evaluate_verifier_grounded
from benchmarking.runtime.observability import observed_duration

Evaluator = Callable[..., Any]
EVALUATORS: dict[str, Evaluator] = {}

DEFAULT_EVALUATORS: dict[str, Evaluator] = {
    "verifier_grounded": evaluate_verifier_grounded,
}


def legacy_evaluators() -> dict[str, Evaluator]:
    """Remaining frozen-service evaluators, loaded only by explicit callers."""
    from benchmarking.scoring.evaluators.generic import evaluate_generic_semantic
    return {
        **DEFAULT_EVALUATORS,
        "generic_semantic": evaluate_generic_semantic,
    }


def register_evaluator(kind: str, evaluator: Evaluator) -> None:
    EVALUATORS[kind] = evaluator


def register_default_evaluators() -> None:
    # Explicit shared callers retain generic scoring, but not retired benchmarks.
    EVALUATORS.update(legacy_evaluators())


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
    if is_retired_benchmark(dataset=getattr(record, "dataset", ""),
                            eval_kind=record.grading.kind,
                            subset=getattr(record.grading, "subset", "")):
        raise EvaluationRegistryError("This benchmark has been retired; scoring is unavailable.")
    registry = EVALUATORS if evaluators is None else evaluators
    evaluator = (evaluator_overrides or {}).get(record.grading.kind) or registry.get(
        record.grading.kind
    )
    if evaluator is None:
        evaluator = registry.get("generic_semantic")
    if evaluator is None:
        raise EvaluationRegistryError(
            f"No evaluator registered for '{record.grading.kind}', and 'generic_semantic' fallback is unavailable."
        )
    return evaluator(
        record,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
        answer_text=answer_text,
        judge=judge,
    )
