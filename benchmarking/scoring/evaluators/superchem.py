from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from benchmarking.core.answer_processing import last_nonempty_line, normalize_space, resolve_candidate_answer_text
from benchmarking.core.convergence import extract_final_answer_line
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.scoring.errors import EvaluationError
from benchmarking.scoring.evaluators._shared import coerce_unit_score
from benchmarking.scoring.results import EvaluationResult


SUPERCHM_XML_CHECKPOINT_RE = re.compile(
    r"<\s*checkpoint\b(?P<attrs>[^>]*)>(?P<body>.*?)</\s*checkpoint\s*>",
    re.IGNORECASE | re.DOTALL,
)
SUPERCHM_INLINE_CHECKPOINT_RE = re.compile(
    r"Checkpoint\s*(?P<index>\d+)\s*[:：-]\s*(?P<body>.*?)(?=(?:\n\s*Checkpoint\s*\d+\s*[:：-])|\Z)",
    re.IGNORECASE | re.DOTALL,
)
SUPERCHM_ATTR_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*["\']([^"\']+)["\']')
SINGLE_LETTER_TOKEN_RE = re.compile(r"\b([A-Z])\b")


def _maybe_json_loads(text: str) -> Any | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def superchem_valid_options(record: BenchmarkRecord) -> tuple[str, ...]:
    options = record.grading.config.get("options") or record.payload.get("options") or {}
    if isinstance(options, dict):
        letters = [str(key).strip().upper() for key in options.keys() if str(key).strip()]
        if letters:
            return tuple(sorted(set(letters)))
    return tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def parse_superchem_option_answer(text: str, *, valid_options: Iterable[str]) -> str:
    valid = tuple(dict.fromkeys(str(item).strip().upper() for item in valid_options if str(item).strip()))
    valid_set = set(valid)
    if not valid_set:
        raise EvaluationError("SUPERChem valid option set is empty.")

    def extract_letters(candidate: Any) -> list[str]:
        if candidate is None:
            return []
        if isinstance(candidate, dict):
            for key in ("answer", "final_answer", "finalAnswer", "choice", "choices"):
                if key in candidate:
                    return extract_letters(candidate[key])
            letters = [str(key).strip().upper() for key in candidate.keys()]
            return [letter for letter in letters if letter in valid_set]
        if isinstance(candidate, list):
            letters: list[str] = []
            for item in candidate:
                letters.extend(extract_letters(item))
            return letters

        raw = str(candidate).strip().upper()
        if not raw:
            return []
        token_matches = [match for match in SINGLE_LETTER_TOKEN_RE.findall(raw) if match in valid_set]
        if token_matches:
            return token_matches
        compact = re.sub(r"[^A-Z]", "", raw)
        if compact and all(letter in valid_set for letter in compact):
            return list(compact)
        return []

    candidates = [
        extract_final_answer_line(text),
        last_nonempty_line(text),
        text,
    ]
    json_payload = _maybe_json_loads(text)
    if json_payload is not None:
        candidates.insert(0, json_payload)
    for candidate in candidates:
        letters = extract_letters(candidate)
        if letters:
            return "|".join(letter for letter in valid if letter in set(letters))
    return ""


def _parse_superchem_checkpoint_weight(attrs: str) -> float:
    weight = 1.0
    for key, value in SUPERCHM_ATTR_RE.findall(attrs):
        if key.lower() in {"weight", "points", "score"}:
            try:
                weight = float(value)
            except ValueError:
                weight = 1.0
    return max(weight, 0.0)


def _parse_superchem_checkpoints(text: str) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for index, match in enumerate(SUPERCHM_XML_CHECKPOINT_RE.finditer(text or ""), start=1):
        body = normalize_space(match.group("body"))
        if not body:
            continue
        checkpoints.append(
            {
                "index": index,
                "weight": _parse_superchem_checkpoint_weight(match.group("attrs") or ""),
                "text": body,
            }
        )
    if checkpoints:
        return checkpoints

    for match in SUPERCHM_INLINE_CHECKPOINT_RE.finditer(text or ""):
        body = normalize_space(match.group("body"))
        if not body:
            continue
        checkpoints.append(
            {
                "index": int(match.group("index")),
                "weight": 1.0,
                "text": body,
            }
        )
    return checkpoints


def evaluate_superchem_multiple_choice_rpf(
    record: BenchmarkRecord,
    *,
    short_answer_text: str,
    full_response_text: str,
    answer_text: str = "",
    judge: Any,
) -> EvaluationResult:
    valid_options = superchem_valid_options(record)
    expected = parse_superchem_option_answer(record.grading.reference_answer, valid_options=valid_options) or record.reference_answer
    candidate_answer_text = resolve_candidate_answer_text(
        answer_text=answer_text,
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )

    checkpoints = _parse_superchem_checkpoints(
        str(record.grading.config.get("reference_reasoning") or record.payload.get("reference_reasoning") or "")
    )
    if not checkpoints:
        raise EvaluationError(f"No SUPERChem checkpoints parsed for record: {record.record_id}")

    rendered_checkpoints = [f"{item['index']}. [weight={item['weight']}] {item['text']}" for item in checkpoints]
    prompt = f"""
You are scoring a chemistry candidate response against expert reasoning checkpoints from SUPERChem.
First decide whether the candidate's final answer matches the reference answer.
For each checkpoint, mark it matched only if the candidate response clearly covers the same reasoning step or conclusion.
Do not award partial matches.
Use the full candidate response; do not rely on any separately extracted short answer.
Return strict JSON only.

Required JSON schema:
{{
  "answer_correct": true,
  "answer_score": 1.0,
  "items": [
    {{"index": 1, "matched": true, "rationale": "brief"}}
  ],
  "summary": "brief overall summary"
}}

QUESTION:
{record.prompt}

REFERENCE ANSWER:
{record.grading.reference_answer}

PARSED REFERENCE OPTION:
{expected}

REFERENCE CHECKPOINTS:
{os.linesep.join(rendered_checkpoints)}

CANDIDATE RESPONSE:
{candidate_answer_text}
""".strip()
    judged = judge.evaluate_json(prompt)
    judged_items = judged.get("items")
    if not isinstance(judged_items, list):
        raise EvaluationError(f"Judge response missing checkpoint items list: {judged}")

    answer_correct = bool(judged.get("answer_correct", judged.get("correct", False)))
    answer_accuracy = coerce_unit_score(
        judged.get("answer_score", judged.get("score")),
        fallback=1.0 if answer_correct else 0.0,
    )

    total_weight = float(sum(float(item["weight"]) for item in checkpoints))
    matched_weight = 0.0
    checkpoint_matches: list[dict[str, Any]] = []
    for checkpoint in checkpoints:
        judged_item = next((item for item in judged_items if int(item.get("index", -1)) == checkpoint["index"]), None)
        matched = bool(judged_item.get("matched")) if isinstance(judged_item, dict) else False
        rationale = "" if not isinstance(judged_item, dict) else str(judged_item.get("rationale") or "")
        if matched:
            matched_weight += float(checkpoint["weight"])
        checkpoint_matches.append(
            {
                "index": checkpoint["index"],
                "weight": float(checkpoint["weight"]),
                "matched": matched,
                "text": checkpoint["text"],
                "rationale": rationale,
            }
        )
    rpf = 0.0 if total_weight <= 0 else matched_weight / total_weight
    return EvaluationResult(
        eval_kind=record.eval_kind,
        score=answer_accuracy,
        max_score=1.0,
        normalized_score=answer_accuracy,
        passed=bool(answer_accuracy),
        primary_metric="answer_accuracy",
        primary_metric_direction="higher_is_better",
        details={
            "method": "judge",
            "parsed_reference": expected,
            "answer_accuracy": answer_accuracy,
            "rpf": rpf,
            "checkpoint_matches": checkpoint_matches,
            "candidate_answer_text": candidate_answer_text,
            "judge": judged,
        },
    )
