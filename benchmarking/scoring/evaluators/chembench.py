from __future__ import annotations

from typing import Any

from benchmarking.core.answer_processing import resolve_candidate_answer_text
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.evaluators._shared import coerce_unit_score
from benchmarking.scoring.results import EvaluationResult


def evaluate_chembench_open_ended(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: Any,
) -> EvaluationResult:
    expected = str(record.grading.reference_answer or record.payload.get("target") or record.reference_answer)
    candidate_answer_text = resolve_candidate_answer_text(
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    prompt = f"""
You are evaluating a ChemBench open-ended chemistry benchmark answer.
Judge whether the candidate answer is correct against the reference answer.
Use the full candidate response; do not rely on any separately extracted short answer.
Allow small numeric tolerance when appropriate for chemistry calculations.
Return strict JSON only.

Required JSON schema:
{{
  "correct": true,
  "score": 1.0,
  "rationale": "brief explanation",
  "expected_answer": "...",
  "candidate_answer": "..."
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
        score=float(score),
        max_score=1.0,
        normalized_score=float(score),
        passed=correct,
        primary_metric="judge_accuracy",
        primary_metric_direction="higher_is_better",
        details={
            "method": "judge",
            "expected": expected,
            "candidate_answer_text": candidate_answer_text,
            "judge": judged,
        },
    )
