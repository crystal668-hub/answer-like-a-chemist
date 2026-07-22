from __future__ import annotations

from typing import Any

from benchmarking.core.answer_processing import resolve_candidate_answer_text
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.results import EvaluationResult


def _hle_correct_value_is_yes(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"yes", "true", "1", "correct"}


def evaluate_hle(
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
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {record.prompt}

[response]: {candidate_answer_text}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {expected}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.

confidence: The extracted confidence score between 0% and 100% from [response]. Put 100 if there is no confidence score available.

Return strict JSON only with keys: extracted_final_answer, reasoning, correct, confidence.
""".strip()
    judged = judge.evaluate_json(prompt)
    correct = _hle_correct_value_is_yes(judged.get("correct"))
    score = 1.0 if correct else 0.0
    confidence = judged.get("confidence")
    try:
        confidence = int(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=score,
        max_score=1.0,
        normalized_score=score,
        passed=correct,
        primary_metric="hle_judge_accuracy",
        primary_metric_direction="higher_is_better",
        details={
            "method": "hle_judge",
            "expected": expected,
            "candidate_answer_text": candidate_answer_text,
            "judge": judged,
            "extracted_final_answer": judged.get("extracted_final_answer"),
            "confidence": confidence,
            "answer_type": record.payload.get("answer_type"),
        },
    )
