from __future__ import annotations

import math
import os
import re
from typing import Any

from benchmarking.core.answer_processing import resolve_candidate_answer_text
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.errors import EvaluationError
from benchmarking.scoring.evaluators._shared import coerce_unit_score
from benchmarking.scoring.results import EvaluationResult


def evaluate_frontierscience_olympiad(
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
You are evaluating a chemistry olympiad benchmark answer.
Decide whether the candidate answer matches the reference answer semantically.
Ignore harmless formatting differences, punctuation, capitalization, and equivalent chemical naming.
Do not give partial credit.
Use the full candidate response; do not rely on any separately extracted short answer.
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
        score=score,
        max_score=1.0,
        normalized_score=score,
        passed=correct,
        primary_metric="semantic_match",
        primary_metric_direction="higher_is_better",
        details={
            "method": "judge",
            "expected": expected,
            "candidate_answer_text": candidate_answer_text,
            "judge": judged,
        },
    )


def parse_frontierscience_research_rubric(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line.startswith("Points:"):
            i += 1
            continue
        match = re.match(r"Points:\s*([0-9]+(?:\.[0-9]+)?)\s*,\s*Item:\s*(.*)", line)
        if not match:
            i += 1
            continue
        points = float(match.group(1))
        description_parts = [match.group(2).strip()]
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("Points:"):
            description_parts.append(lines[i].rstrip())
            i += 1
        description = "\n".join(part for part in description_parts if part is not None).strip()
        items.append({"points": points, "description": description})
    return items


def evaluate_frontierscience_research(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: Any,
) -> EvaluationResult:
    rubric_items = parse_frontierscience_research_rubric(record.grading.reference_answer)
    if not rubric_items:
        raise EvaluationError(f"No rubric items parsed for record: {record.record_id}")
    rubric_lines = [f"{idx + 1}. [{item['points']} points] {item['description']}" for idx, item in enumerate(rubric_items)]
    max_score = float(sum(item["points"] for item in rubric_items))
    candidate_answer_text = resolve_candidate_answer_text(
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    prompt = f"""
You are grading a chemistry research benchmark response against a point rubric.
For each rubric item, award either 0 or the item's full points only.
Do not invent extra rubric items.
Return strict JSON only.

Required JSON schema:
{{
  "items": [
    {{"index": 1, "awarded": 1.0, "max_points": 1.0, "met": true, "rationale": "brief"}}
  ],
  "total_awarded": 0.0,
  "max_points": {max_score},
  "summary": "brief overall summary"
}}

QUESTION:
{record.prompt}

RUBRIC ITEMS:
{os.linesep.join(rubric_lines)}

CANDIDATE ANSWER:
{candidate_answer_text}
""".strip()
    judged = judge.evaluate_json(prompt)
    judged_items = judged.get("items")
    if not isinstance(judged_items, list):
        raise EvaluationError(f"Judge response missing items list: {judged}")

    awarded_items: list[dict[str, Any]] = []
    total_awarded = 0.0
    for idx, rubric_item in enumerate(rubric_items, start=1):
        judged_item = next((item for item in judged_items if int(item.get("index", -1)) == idx), None)
        if not isinstance(judged_item, dict):
            awarded = 0.0
            rationale = "Judge omitted this rubric item; treated as unmet."
            met = False
        else:
            met = bool(judged_item.get("met"))
            awarded = float(judged_item.get("awarded") or 0.0)
            max_points = float(rubric_item["points"])
            awarded = max(0.0, min(max_points, awarded))
            if met and not math.isclose(awarded, max_points, rel_tol=1e-9, abs_tol=1e-9):
                awarded = max_points
            if not met:
                awarded = 0.0
            rationale = str(judged_item.get("rationale") or "")
        total_awarded += awarded
        awarded_items.append(
            {
                "index": idx,
                "awarded": awarded,
                "max_points": float(rubric_item["points"]),
                "met": met,
                "description": rubric_item["description"],
                "rationale": rationale,
            }
        )

    normalized_score = 0.0 if max_score <= 0 else total_awarded / max_score
    full_credit = bool(awarded_items) and math.isclose(total_awarded, max_score, rel_tol=1e-9, abs_tol=1e-9)
    full_credit = full_credit and all(
        bool(item["met"]) and math.isclose(float(item["awarded"]), float(item["max_points"]), rel_tol=1e-9, abs_tol=1e-9)
        for item in awarded_items
    )
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=total_awarded,
        max_score=max_score,
        normalized_score=normalized_score,
        passed=full_credit,
        primary_metric="rubric_points",
        primary_metric_direction="higher_is_better",
        details={
            "method": "judge",
            "judge": judged,
            "candidate_answer_text": candidate_answer_text,
            "rubric_items": awarded_items,
            "summary": judged.get("summary"),
        },
    )
