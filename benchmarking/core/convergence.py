from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


FINAL_ANSWER_LINE_RE = re.compile(
    r"^\s*(?P<marker>\*\*)?\s*FINAL\s+ANSWER\s*[:：-](?P<answer>.*)$",
    re.IGNORECASE,
)
HLE_ANSWER_RE = re.compile(r"(?ims)^\s*Explanation\s*:.+^\s*Answer\s*:.+^\s*Confidence\s*:\s*\S+")
MARKDOWN_BOLD_MARKER = "**"
TIMEOUT_FAMILY_HTTP_STATUS_RE = re.compile(
    r"(?:\bhttp(?:\s+status)?\s*(?:408|499|500|502|503|504)\b)"
    r"|(?:\bstatus\s*(?:408|499|500|502|503|504)\b)"
    r"|(?:\b(?:408|499|500|502|503|504)\b.*\b(?:gateway|server|service|request|response|upstream|http)\b)",
    re.IGNORECASE,
)
TIMEOUT_FAMILY_POSITIVE_PATTERNS = (
    "timeout",
    "timed out",
    "deadline exceeded",
    "context deadline exceeded",
    "gateway timeout",
    "etimedout",
    "esockettimedout",
    "econnreset",
    "econnaborted",
    "econnrefused",
    "enetunreach",
    "eai_again",
    "fetch failed",
    "network request failed",
    "connection reset",
    "connection error",
    "network error",
)
TIMEOUT_FAMILY_EXCLUSION_PATTERNS = (
    "approval timeout",
    "approval timed out",
    "timed out waiting for approval",
    "sandbox timeout",
    "sandbox timed out",
    "exec timeout",
    "exec timed out",
    "tool timeout",
    "tool timed out",
    "skill script timeout",
    "skill script timed out",
    "script timeout",
    "script timed out",
    "auth failed",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "billing",
    "insufficient_quota",
    "quota exceeded",
    "rate limit",
    "ratelimit",
    "too many requests",
    "context overflow",
    "context length",
    "maximum context",
    "max context",
    "model not found",
    "image size",
    "role ordering",
    "invalid role",
    "format error",
    "invalid format",
    "response_format",
    "invalid_request_error",
)
TOOL_RESULT_ERROR_PATTERNS = (
    "enoent",
    '"status": "error"',
    "'status': 'error'",
    '"error":',
    "'error':",
    "usage:",
    "traceback",
    "modulenotfounderror",
    "no module named",
    "unrecognized arguments",
    "the following arguments are required",
    "required: --request-json",
    "missing_request_json",
    "invalid_molecule",
    "request json",
)
REQUEST_SHAPE_ERROR_PATTERNS = (
    "usage:",
    "--request-json",
    "missing_request_json",
    "invalid_molecule",
    "unrecognized arguments",
    "the following arguments are required",
    "request json",
    "malformed json",
    "invalid input",
)
MISSING_SKILL_DOC_PATTERNS = (
    "skills/benchmark-solving-protocol/skill.md",
    "benchmark-solving-protocol/skill.md",
)
CHECKLIST_STATE_RE = re.compile(r"\b(?:todo|done|blocked)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ConvergencePolicy:
    timeout_seconds: int
    stop_fraction: float = 0.2
    finalization_grace_seconds: int = 90
    max_unchanged_status_polls: int = 2
    max_recovery_attempts: int = 2

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)


def _iter_transcript_messages(transcript_path: Path) -> list[dict[str, Any]]:
    return [event["message"] for event in _iter_transcript_events(transcript_path) if isinstance(event.get("message"), dict)]


def _iter_transcript_events(transcript_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return messages
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            messages.append(event)
    return messages


def _text_from_content(content: Any) -> str:
    parts: list[str] = []
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def summarize_transcript_convergence(transcript_path: Path) -> dict[str, Any]:
    assistant_turn_count = 0
    tool_call_count = 0
    tool_names: list[str] = []
    prompt_errors: list[str] = []
    missing_skill_doc_read_count = 0
    tool_result_error_count = 0
    request_shape_error_count = 0
    coverage_checklist_present = False
    for event in _iter_transcript_events(transcript_path):
        if event.get("customType") == "openclaw:prompt-error":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            error_text = str(data.get("error") or data.get("message") or "").strip()
            if error_text:
                prompt_errors.append(error_text)
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") == "assistant":
            assistant_turn_count += 1
            assistant_text = _text_from_content(message.get("content"))
            if "coverage checklist" in assistant_text.lower() and CHECKLIST_STATE_RE.search(assistant_text):
                coverage_checklist_present = True
            for item in message.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    tool_call_count += 1
                    name = str(item.get("name") or "")
                    if name:
                        tool_names.append(name)
        elif message.get("role") == "toolResult":
            tool_text = _text_from_content(message.get("content"))
            normalized = tool_text.lower()
            if any(pattern in normalized for pattern in MISSING_SKILL_DOC_PATTERNS):
                missing_skill_doc_read_count += 1
            if _looks_like_tool_result_error(normalized):
                tool_result_error_count += 1
            if _looks_like_request_shape_error(normalized):
                request_shape_error_count += 1
    latest_prompt_error = prompt_errors[-1] if prompt_errors else ""
    return {
        "transcript_path": str(transcript_path),
        "assistant_turn_count": assistant_turn_count,
        "tool_call_count": tool_call_count,
        "tool_names": tool_names,
        "prompt_error_count": len(prompt_errors),
        "latest_prompt_error": latest_prompt_error,
        "latest_prompt_error_is_timeout": is_timeout_family_text(latest_prompt_error),
        "missing_skill_doc_read_count": missing_skill_doc_read_count,
        "tool_result_error_count": tool_result_error_count,
        "request_shape_error_count": request_shape_error_count,
        "coverage_checklist_present": coverage_checklist_present,
    }


def _looks_like_tool_result_error(normalized_text: str) -> bool:
    candidate = str(normalized_text or "")
    return any(pattern in candidate for pattern in TOOL_RESULT_ERROR_PATTERNS)


def _looks_like_request_shape_error(normalized_text: str) -> bool:
    candidate = str(normalized_text or "")
    return any(pattern in candidate for pattern in REQUEST_SHAPE_ERROR_PATTERNS)


def is_timeout_family_text(text: Any) -> bool:
    candidate = str(text or "").strip().lower()
    if not candidate:
        return False
    if any(pattern in candidate for pattern in TIMEOUT_FAMILY_EXCLUSION_PATTERNS):
        return False
    if TIMEOUT_FAMILY_HTTP_STATUS_RE.search(candidate):
        return True
    return any(pattern in candidate for pattern in TIMEOUT_FAMILY_POSITIVE_PATTERNS)


def _strip_final_answer_emphasis(answer: str, marker: str) -> str:
    candidate = str(answer or "").strip()
    if marker:
        if candidate.startswith(marker):
            candidate = candidate[len(marker) :].strip()
        if candidate.endswith(marker):
            candidate = candidate[: -len(marker)].strip()
        if candidate == marker:
            return ""
    if candidate == MARKDOWN_BOLD_MARKER:
        return ""
    if candidate.startswith(MARKDOWN_BOLD_MARKER) and candidate.endswith(MARKDOWN_BOLD_MARKER):
        inner = candidate[len(MARKDOWN_BOLD_MARKER) : -len(MARKDOWN_BOLD_MARKER)].strip()
        if inner:
            return inner
    return candidate


def extract_final_answer_line(text: str) -> str:
    answers: list[str] = []
    for line in str(text or "").splitlines():
        match = FINAL_ANSWER_LINE_RE.match(line)
        if not match:
            continue
        answer = _strip_final_answer_emphasis(match.group("answer"), str(match.group("marker") or ""))
        if answer:
            answers.append(answer)
    return answers[-1] if answers else ""


def has_final_answer_marker(text: str) -> bool:
    return bool(extract_final_answer_line(text))


def is_complete_benchmark_answer(text: str) -> bool:
    candidate = str(text or "").strip()
    return bool(has_final_answer_marker(candidate) or HLE_ANSWER_RE.search(candidate))


def extract_latest_complete_answer_from_transcript(transcript_path: Path) -> str:
    for message in reversed(_iter_transcript_messages(transcript_path)):
        if message.get("role") != "assistant":
            continue
        text = _text_from_content(message.get("content"))
        if is_complete_benchmark_answer(text):
            return text
    return ""
