from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmarking.core.datasets import BenchmarkRecord


@dataclass
class EvaluationResult:
    eval_kind: str
    score: float
    max_score: float
    normalized_score: float
    passed: bool | None
    primary_metric: str
    primary_metric_direction: str
    details: dict[str, Any]


def build_execution_error_evaluation(record: BenchmarkRecord, *, error_message: str) -> EvaluationResult:
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=0.0,
        max_score=1.0,
        normalized_score=0.0,
        passed=False,
        primary_metric="execution_error",
        primary_metric_direction="higher_is_better",
        details={
            "method": "execution_error",
            "error": error_message,
        },
    )
