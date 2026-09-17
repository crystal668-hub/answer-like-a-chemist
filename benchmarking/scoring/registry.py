from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.evaluators.verifier_grounded import evaluate_verifier_grounded
from benchmarking.runtime.observability import observed_duration

Evaluator = Callable[..., Any]
EVALUATORS: dict[str, Evaluator] = {}

DEFAULT_EVALUATORS: dict[str, Evaluator] = {
    "verifier_grounded": evaluate_verifier_grounded,
}


def legacy_evaluators() -> dict[str, Evaluator]:
    """Shared historical scoring API, loaded only by explicit callers."""
    from benchmarking.scoring.evaluators.chembench import evaluate_chembench_open_ended
    from benchmarking.scoring.evaluators.frontierscience import (
        evaluate_frontierscience_olympiad, evaluate_frontierscience_research,
    )
    from benchmarking.scoring.evaluators.generic import evaluate_generic_semantic
    from benchmarking.scoring.evaluators.hle import evaluate_hle
    from benchmarking.scoring.evaluators.superchem import evaluate_superchem_multiple_choice_rpf
    return {
        **DEFAULT_EVALUATORS,
        "chembench_open_ended": evaluate_chembench_open_ended,
        "frontierscience_olympiad": evaluate_frontierscience_olympiad,
        "frontierscience_research": evaluate_frontierscience_research,
        "superchem_multiple_choice_rpf": evaluate_superchem_multiple_choice_rpf,
        "hle": evaluate_hle,
        "generic_semantic": evaluate_generic_semantic,
    }


def register_evaluator(kind: str, evaluator: Evaluator) -> None:
    EVALUATORS[kind] = evaluator


def register_default_evaluators() -> None:
    # Preserve the explicit public shared-scoring registration contract.
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
