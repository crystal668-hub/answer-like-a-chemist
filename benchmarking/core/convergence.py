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
RESCUE_FINAL_ANSWER_MARKER_ONLY_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*FINAL\s+ANSWER\s*(?:[:：-]\s*)?(?:\*\*)?\s*$",
    re.IGNORECASE,
)
RESEARCH_FINAL_MARKER_PATTERN = (
    r"FINAL\s+RESEARCH\s+ANSWER|"
    r"FINAL\s+RESEARCH\s+RESPONSE|"
    r"FINAL\s+RESEARCH\s+SYNTHESIS|"
    r"FINAL\s+RESEARCH\s+CONCLUSION|"
    r"RESEARCH\s+FINAL\s+ANSWER"
)
RESEARCH_FINAL_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:\d+(?:\.\d+)*[.)]?\s*)?"
    r"(?P<marker>"
    + RESEARCH_FINAL_MARKER_PATTERN
    + r")"
    r"\s*(?:[:：-]\s*)?(?:\*\*)?\s*(?P<answer>.*)$",
    re.IGNORECASE,
)
RESCUE_RESEARCH_SECTION_MARKER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:\d+(?:\.\d+)*[.)]?\s*)?"
    r"(?P<marker>"
    + RESEARCH_FINAL_MARKER_PATTERN
    + r"|FINAL\s+ANSWER|FINAL\s+SYNTHESIS|FINAL(?:\s*/\s*|\s+AND\s+|\s+)CONCLUSION|"
    r"SUPPORTED\s+CONCLUSION|CONCLUSION"
    r")"
    r"\s*(?:[:：-]\s*)?(?:\*\*)?\s*(?P<answer>.*)$",
    re.IGNORECASE,
)
HLE_ANSWER_RE = re.compile(r"(?ims)^\s*Explanation\s*:.+^\s*Answer\s*:.+^\s*Confidence\s*:\s*\S+")
MARKDOWN_BOLD_MARKER = "**"
FRONTIERSCIENCE_RESEARCH_EVAL_KIND = "frontierscience_research"
RESEARCH_RESCUE_PROCESS_ONLY_HEADINGS = (
    "reference",
    "references",
    "coverage checklist",
    "coverage verification",
    "evidence ledger",
    "checks",
    "visible derivation",
    "visible derivation and checks",
)
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
    "no such file or directory",
    '"status": "error"',
    "'status': 'error'",
    '"available": false',
    "'available': false",
    '"error_kind"',
    "'error_kind'",
    '"error":',
    "'error':",
    "traceback",
    "modulenotfounderror",
    "no module named",
    "command exited with code",
    "web fetch failed",
    "duckduckgo returned",
    "exec preflight:",
)
REQUEST_SHAPE_ERROR_PATTERNS = (
    "exec preflight: complex interpreter invocation detected",
    "unrecognized arguments",
    "the following arguments are required",
    "required: --request-json",
    "missing_request_json",
    "missing required request",
    "missing required field",
    "malformed json",
    "invalid json",
    "invalid_request",
    "invalid_molecule",
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
            tool_name = str(message.get("toolName") or "")
            normalized = tool_text.lower()
            if any(pattern in normalized for pattern in MISSING_SKILL_DOC_PATTERNS):
                missing_skill_doc_read_count += 1
            if _looks_like_tool_result_error(normalized, tool_name=tool_name):
                tool_result_error_count += 1
            if _looks_like_request_shape_error(normalized, tool_name=tool_name):
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


def _looks_like_tool_result_error(normalized_text: str, *, tool_name: str = "") -> bool:
    candidate = str(normalized_text or "")
    if not candidate:
        return False
    if str(tool_name or "").strip().lower() == "read":
        return any(
            pattern in candidate
            for pattern in (
                "enoent",
                "no such file or directory",
                '"status": "error"',
                "'status': 'error'",
                '"available": false',
                "'available': false",
                '"error":',
                "'error':",
            )
        )
    if _looks_like_cli_usage_error(candidate):
        return True
    return any(pattern in candidate for pattern in TOOL_RESULT_ERROR_PATTERNS)


def _looks_like_request_shape_error(normalized_text: str, *, tool_name: str = "") -> bool:
    candidate = str(normalized_text or "")
    if not candidate:
        return False
    if str(tool_name or "").strip().lower() == "read":
        return False
    if _looks_like_cli_usage_error(candidate):
        return True
    if "request json" in candidate and any(
        marker in candidate for marker in ("missing", "invalid", "malformed", "required")
    ):
        return True
    return any(pattern in candidate for pattern in REQUEST_SHAPE_ERROR_PATTERNS)


def _looks_like_cli_usage_error(normalized_text: str) -> bool:
    candidate = str(normalized_text or "")
    usage_markers = (
        "error:",
        "unrecognized arguments",
        "the following arguments are required",
        "required:",
    )
    required_arg_markers = ("--request-json", "--output-dir")
    return "usage:" in candidate and (
        any(marker in candidate for marker in required_arg_markers)
        or any(marker in candidate for marker in usage_markers)
    )


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


def has_research_final_marker(text: str) -> bool:
    return any(RESEARCH_FINAL_MARKER_RE.match(line) for line in str(text or "").splitlines())


def is_complete_benchmark_answer(text: str) -> bool:
    candidate = str(text or "").strip()
    return bool(has_final_answer_marker(candidate) or HLE_ANSWER_RE.search(candidate))


def _strip_markdown_heading(line: str) -> str:
    candidate = str(line or "").strip()
    candidate = re.sub(r"^\s*#{1,6}\s*", "", candidate).strip()
    if candidate.startswith(MARKDOWN_BOLD_MARKER) and candidate.endswith(MARKDOWN_BOLD_MARKER):
        candidate = candidate[len(MARKDOWN_BOLD_MARKER) : -len(MARKDOWN_BOLD_MARKER)].strip()
    return candidate.strip()


def _strip_common_markdown(line: str) -> str:
    candidate = _strip_markdown_heading(line)
    while candidate.startswith(MARKDOWN_BOLD_MARKER):
        candidate = candidate[len(MARKDOWN_BOLD_MARKER) :].strip()
    while candidate.endswith(MARKDOWN_BOLD_MARKER):
        candidate = candidate[: -len(MARKDOWN_BOLD_MARKER)].strip()
    return candidate.strip()


def _is_markdown_divider(line: str) -> bool:
    candidate = str(line or "").strip()
    if not candidate:
        return True
    if re.fullmatch(r"[-*_]{3,}", candidate):
        return True
    if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", candidate):
        return True
    return False


def _is_process_only_research_heading(line: str) -> bool:
    candidate = _strip_common_markdown(line).strip(" :：-").lower()
    if not candidate:
        return True
    return any(candidate == heading or candidate.startswith(f"{heading}:") for heading in RESEARCH_RESCUE_PROCESS_ONLY_HEADINGS)


def _has_substantive_rescue_answer_text(text: str) -> bool:
    candidate = re.sub(r"\s+", " ", str(text or "")).strip()
    return len(candidate) >= 12 and bool(re.search(r"[A-Za-z0-9]", candidate))


def _research_rescue_block_after_marker_is_complete(lines: list[str], marker_index: int) -> bool:
    meaningful: list[str] = []
    for line in lines[marker_index + 1 :]:
        stripped = str(line or "").strip()
        if not stripped or _is_markdown_divider(stripped):
            continue
        if not meaningful and _is_process_only_research_heading(stripped):
            return False
        meaningful.append(stripped)
    if not meaningful:
        return False
    return _has_substantive_rescue_answer_text("\n".join(meaningful))


def _is_complete_frontierscience_research_rescue_answer(text: str) -> bool:
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        if RESCUE_FINAL_ANSWER_MARKER_ONLY_RE.match(line):
            if _research_rescue_block_after_marker_is_complete(lines, index):
                return True
            continue
        match = RESCUE_RESEARCH_SECTION_MARKER_RE.match(line)
        if not match:
            continue
        inline_answer = _strip_common_markdown(str(match.group("answer") or ""))
        if _has_substantive_rescue_answer_text(inline_answer):
            return True
        if _research_rescue_block_after_marker_is_complete(lines, index):
            return True
    return False


def is_complete_rescue_answer(text: str, *, eval_kind: str = "") -> bool:
    candidate = str(text or "").strip()
    if is_complete_benchmark_answer(candidate):
        return True
    if str(eval_kind or "").strip() != FRONTIERSCIENCE_RESEARCH_EVAL_KIND:
        return False
    return _is_complete_frontierscience_research_rescue_answer(candidate)


def is_complete_answer_for_eval(text: str, *, eval_kind: str = "") -> bool:
    candidate = str(text or "").strip()
    if is_complete_benchmark_answer(candidate):
        return True
    if str(eval_kind or "").strip() != FRONTIERSCIENCE_RESEARCH_EVAL_KIND:
        return False
    return _is_complete_frontierscience_research_rescue_answer(candidate)


def extract_latest_complete_answer_from_transcript(transcript_path: Path) -> str:
    return extract_latest_complete_answer_from_transcript_for_eval(transcript_path, eval_kind="")


def extract_latest_complete_answer_from_transcript_for_eval(transcript_path: Path, *, eval_kind: str = "") -> str:
    for message in reversed(_iter_transcript_messages(transcript_path)):
        if message.get("role") != "assistant":
            continue
        text = _text_from_content(message.get("content"))
        if is_complete_answer_for_eval(text, eval_kind=eval_kind):
            return text
    return ""
