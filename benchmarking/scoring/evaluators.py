from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

from benchmarking.core.datasets import BenchmarkRecord


FINAL_ANSWER_RE = re.compile(r"^\s*FINAL\s+ANSWER\s*[:：-]\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
NUMBER_RE = re.compile(r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?")
NUMERIC_SCALAR_ANSWER_RE = re.compile(
    r"""
    ^\s*
    [-+]?
    (?:
        (?:
            (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?
            |
            \.\d+
        )
        (?:
            \s*(?:[eE]|[xX×]\s*10\^?)\s*[-+]?\d+
        )?
    )
    \s*$
    """,
    re.VERBOSE,
)
JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL | re.IGNORECASE)
INVALID_JSON_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrtu])')
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


class EvaluationError(RuntimeError):
    pass


@dataclass
class EvaluationResult:
    eval_kind: str
    score: float
    max_score: float
    normalized_score: float
    passed: bool
    primary_metric: str
    primary_metric_direction: str
    details: dict[str, Any]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_loose(text: str) -> str:
    text = normalize_space(text).lower()
    text = text.replace("µ", "u")
    text = re.sub(r"[\s\.,;:!?'\"`~()\[\]{}<>]+", "", text)
    return text


def last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def extract_final_answer_line(text: str) -> str:
    matches = FINAL_ANSWER_RE.findall(text)
    if matches:
        return matches[-1].strip()
    return ""


def extract_candidate_short_answer(text: str) -> str:
    final_answer = extract_final_answer_line(text)
    if final_answer:
        return final_answer
    last_line = last_nonempty_line(text)
    if last_line and len(last_line) <= 200:
        return last_line
    return normalize_space(text)


def normalize_answer_tracks(*, short_answer_text: str = "", full_response_text: str = "") -> tuple[str, str]:
    short_text = str(short_answer_text or "").strip()
    full_text = str(full_response_text or "").strip()
    if not short_text and full_text:
        short_text = extract_candidate_short_answer(full_text)
    if not full_text and short_text:
        full_text = f"FINAL ANSWER: {short_text}"
    return short_text, full_text


def resolve_candidate_answer_text(
    *,
    answer_text: str = "",
    short_answer_text: str = "",
    full_response_text: str = "",
) -> str:
    candidate = str(answer_text or "").strip()
    if candidate:
        return candidate
    short_text, full_text = normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=full_response_text)
    return full_text or short_text


def parse_numeric_scalar(text: str) -> float | None:
    if not text:
        return None
    candidate = extract_final_answer_line(text) or text
    candidate = candidate.replace("×10^", "e").replace("x10^", "e")
    matches = NUMBER_RE.findall(candidate)
    if not matches:
        return None
    token = matches[0].replace(",", "")
    try:
        return float(token)
    except ValueError:
        return None


def is_numeric_scalar_answer(text: str) -> bool:
    candidate = extract_final_answer_line(text) or str(text or "").strip()
    if not candidate:
        return False
    candidate = candidate.strip()
    if candidate.startswith("$") and candidate.endswith("$"):
        candidate = candidate[1:-1].strip()
    boxed_match = re.fullmatch(r"\\boxed\{([^{}]+)\}", candidate)
    if boxed_match:
        candidate = boxed_match.group(1).strip()
    return bool(NUMERIC_SCALAR_ANSWER_RE.fullmatch(candidate))


def safe_json_extract(text: str) -> Any:
    def loads_with_repair(candidate: str) -> Any:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            if "Invalid \\escape" not in str(exc):
                raise
            repaired = INVALID_JSON_BACKSLASH_RE.sub(r"\\\\", candidate)
            return json.loads(repaired)

    stripped = text.strip()
    if not stripped:
        raise EvaluationError("Cannot extract JSON from empty judge response.")
    for candidate in (stripped,):
        try:
            return loads_with_repair(candidate)
        except json.JSONDecodeError:
            pass
    match = JSON_BLOCK_RE.search(stripped)
    if match:
        return loads_with_repair(match.group(1))

    lines = stripped.splitlines()
    for index, line in enumerate(lines):
        candidate = line.lstrip()
        if candidate.startswith("{") or candidate.startswith("["):
            fragment = "\n".join(lines[index:]).strip()
            for end in range(len(fragment), 0, -1):
                try:
                    return loads_with_repair(fragment[:end])
                except json.JSONDecodeError:
                    continue
            break

    brace_positions = [idx for idx in (stripped.find("{"), stripped.rfind("{")) if idx != -1]
    for start in brace_positions:
        fragment = stripped[start:]
        for end in range(len(fragment), 0, -1):
            try:
                return loads_with_repair(fragment[:end])
            except json.JSONDecodeError:
                continue
    raise EvaluationError(f"Judge response did not contain parseable JSON:\n{text}")


def maybe_json_loads(text: str) -> Any | None:
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
    json_payload = maybe_json_loads(text)
    if json_payload is not None:
        candidates.insert(0, json_payload)
    for candidate in candidates:
        letters = extract_letters(candidate)
        if letters:
            return "|".join(letter for letter in valid if letter in set(letters))
    return ""


def parse_superchem_checkpoint_weight(attrs: str) -> float:
    weight = 1.0
    for key, value in SUPERCHM_ATTR_RE.findall(attrs):
        if key.lower() in {"weight", "points", "score"}:
            try:
                weight = float(value)
            except ValueError:
                weight = 1.0
    return max(weight, 0.0)


def parse_superchem_checkpoints(text: str) -> list[dict[str, Any]]:
    checkpoints: list[dict[str, Any]] = []
    for index, match in enumerate(SUPERCHM_XML_CHECKPOINT_RE.finditer(text or ""), start=1):
        body = normalize_space(match.group("body"))
        if not body:
            continue
        checkpoints.append(
            {
                "index": index,
                "weight": parse_superchem_checkpoint_weight(match.group("attrs") or ""),
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
    raw_score = judged.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0 if correct else 0.0
    score = max(0.0, min(1.0, score))

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


def heuristic_semantic_match(expected: str, predicted: str) -> bool | None:
    expected_short = extract_candidate_short_answer(expected)
    predicted_short = extract_candidate_short_answer(predicted)
    if not expected_short or not predicted_short:
        return None
    if is_numeric_scalar_answer(expected_short) and is_numeric_scalar_answer(predicted_short):
        expected_num = parse_numeric_scalar(expected_short)
        predicted_num = parse_numeric_scalar(predicted_short)
        if expected_num is None or predicted_num is None:
            return None
        return math.isclose(expected_num, predicted_num, rel_tol=1e-4, abs_tol=1e-8)
    expected_norm = normalize_loose(expected_short)
    predicted_norm = normalize_loose(predicted_short)
    if expected_norm == predicted_norm:
        return True

    def safe_contains(needle: str, haystack: str) -> bool:
        if not needle or len(needle) < 3:
            return False
        if is_numeric_scalar_answer(needle):
            return False
        return needle in haystack

    if safe_contains(expected_norm, predicted_norm):
        return True
    if safe_contains(predicted_norm, expected_norm):
        return True
    return None


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
    raw_score = judged.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0 if correct else 0.0
    score = max(0.0, min(1.0, score))
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

    checkpoints = parse_superchem_checkpoints(str(record.grading.config.get("reference_reasoning") or record.payload.get("reference_reasoning") or ""))
    if not checkpoints:
        raise EvaluationError(f"No SUPERChem checkpoints parsed for record: {record.record_id}")

    rendered_checkpoints = [
        f"{item['index']}. [weight={item['weight']}] {item['text']}"
        for item in checkpoints
    ]
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
    raw_answer_score = judged.get("answer_score", judged.get("score"))
    try:
        answer_accuracy = float(raw_answer_score)
    except (TypeError, ValueError):
        answer_accuracy = 1.0 if answer_correct else 0.0
    answer_accuracy = max(0.0, min(1.0, answer_accuracy))

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
    raw_score = judged.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 1.0 if correct else 0.0
    score = max(0.0, min(1.0, score))
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
