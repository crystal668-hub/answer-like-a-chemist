from __future__ import annotations

import json
import re
from typing import Any

from benchmarking.core.convergence import extract_final_answer_line


JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", re.DOTALL | re.IGNORECASE)
INVALID_JSON_BACKSLASH_RE = re.compile(r'\\(?!["\\/bfnrtu])')


class AgentResponseParseError(ValueError):
    pass


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
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
    short_text, full_text = normalize_answer_tracks(
        short_answer_text=short_answer_text,
        full_response_text=full_response_text,
    )
    return full_text or short_text


def parse_agent_json_response(text: str) -> dict[str, Any]:
    def loads_with_repair(candidate: str) -> Any:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            if "Invalid \\escape" not in str(exc):
                raise
            repaired = INVALID_JSON_BACKSLASH_RE.sub(r"\\\\", candidate)
            return json.loads(repaired)

    def require_object(candidate: Any) -> dict[str, Any]:
        if not isinstance(candidate, dict):
            raise AgentResponseParseError("Agent response JSON must be an object.")
        return candidate

    stripped = text.strip()
    if not stripped:
        raise AgentResponseParseError("Cannot extract JSON from an empty agent response.")
    try:
        return require_object(loads_with_repair(stripped))
    except json.JSONDecodeError:
        pass

    match = JSON_BLOCK_RE.search(stripped)
    if match:
        try:
            return require_object(loads_with_repair(match.group(1)))
        except json.JSONDecodeError:
            pass

    lines = stripped.splitlines()
    for index, line in enumerate(lines):
        candidate = line.lstrip()
        if candidate.startswith("{") or candidate.startswith("["):
            fragment = "\n".join(lines[index:]).strip()
            for end in range(len(fragment), 0, -1):
                try:
                    return require_object(loads_with_repair(fragment[:end]))
                except json.JSONDecodeError:
                    continue
            break

    brace_positions = [idx for idx in (stripped.find("{"), stripped.rfind("{")) if idx != -1]
    for start in brace_positions:
        fragment = stripped[start:]
        for end in range(len(fragment), 0, -1):
            try:
                return require_object(loads_with_repair(fragment[:end]))
            except json.JSONDecodeError:
                continue
    raise AgentResponseParseError(f"Agent response did not contain parseable JSON:\n{text}")
