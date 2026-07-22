from __future__ import annotations

from typing import Any

from benchmarking.core.answer_processing import resolve_candidate_answer_text
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.evaluators._shared import coerce_unit_score
from benchmarking.scoring.results import EvaluationResult


def evaluate_generic_semantic(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: Any,
) -> EvaluationResult:
    expected = record.grading.reference_answer
    candidate_answer_text = resolve_candidate_answer_text(
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    prompt = f"""
You are evaluating whether a benchmark candidate answer matches a reference answer.
Use the full candidate response; do not rely on any separately extracted short answer.
Return strict JSON only.

Required JSON schema:
{{
  "correct": true,
  "score": 1.0,
  "rationale": "brief explanation"
}}

QUESTION:
{record.prompt}

REFERENCE ANSWER:
{expected}

CANDIDATE ANSWER:
{candidate_answer_text}
""".strip()
    judged = judge.evaluate_json(prompt)
    correct = bool(judged.get("correct"))
    score = coerce_unit_score(judged.get("score"), fallback=1.0 if correct else 0.0)
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=score,
        max_score=1.0,
        normalized_score=score,
        passed=correct,
        primary_metric="semantic_match",
        primary_metric_direction="higher_is_better",
        details={"method": "judge", "judge": judged, "expected": expected, "candidate_answer_text": candidate_answer_text},
    )
