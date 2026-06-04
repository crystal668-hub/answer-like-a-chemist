from __future__ import annotations

from typing import Any, Callable

from benchmarking.core.datasets import BenchmarkRecord


Evaluator = Callable[..., Any]
EVALUATORS: dict[str, Evaluator] = {}


class EvaluationRegistryError(LookupError):
    pass


def register_evaluator(kind: str, evaluator: Evaluator) -> None:
    EVALUATORS[kind] = evaluator


def register_default_evaluators() -> None:
    from benchmarking.scoring import evaluators

    register_evaluator("chembench_open_ended", evaluators.evaluate_chembench_open_ended)
    register_evaluator("frontierscience_olympiad", evaluators.evaluate_frontierscience_olympiad)
    register_evaluator("frontierscience_research", evaluators.evaluate_frontierscience_research)
    register_evaluator("superchem_multiple_choice_rpf", evaluators.evaluate_superchem_multiple_choice_rpf)
    register_evaluator("hle", evaluators.evaluate_hle)
    register_evaluator("verifier_grounded", evaluators.evaluate_verifier_grounded)
    register_evaluator("generic_semantic", evaluators.evaluate_generic_semantic)


def evaluate_record(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: object,
) -> Any:
    evaluator = EVALUATORS.get(record.grading.kind)
    if evaluator is None:
        evaluator = EVALUATORS.get("generic_semantic")
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
