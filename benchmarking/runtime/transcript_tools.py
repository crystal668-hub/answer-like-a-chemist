from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_BLOCKED_MARKERS = (
    "benchmark_workspace_guard_blocked",
    "benchmark_workdir_invalid",
    "blocked by benchmark-workdir-guard",
)
_BACKGROUND_SESSION_RE = re.compile(r"Command still running \(session ([^,)]+)")
_EXIT_CODE_RES = (
    re.compile(r"Command exited with code\s+(-?\d+)"),
    re.compile(r"Process exited with code\s+(-?\d+)"),
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)"
    r"(\s*[=:]\s*|\s+)([^\s,;]+)"
)
_SECRET_OPTION_RE = re.compile(
    r"(?i)(--(?:api[-_]?key|authorization|password|secret|token))(?:=|\s+)([^\s]+)"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@")


@dataclass(frozen=True)
class ToolEvent:
    tool_call_id: str
    tool_name: str
    arguments: Any
    call_line: int
    call_timestamp_ms: int | None = None
    result_line: int | None = None
    result_timestamp_ms: int | None = None
    result: Mapping[str, Any] | None = None


def _timestamp_ms(payload: Mapping[str, Any], message: Mapping[str, Any]) -> int | None:
    for value in (message.get("timestamp"), payload.get("timestamp")):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def tool_events_from_transcript(
    payloads: list[tuple[int, Any]],
) -> tuple[list[ToolEvent], list[tuple[int, Mapping[str, Any]]]]:
    pending: list[ToolEvent] = []
    results: dict[str, tuple[int, int | None, Mapping[str, Any]]] = {}
    result_order: list[tuple[int, Mapping[str, Any]]] = []
    for line_number, payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        message = payload.get("message")
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role == "assistant":
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item_index, item in enumerate(content):
                if not isinstance(item, Mapping) or str(item.get("type") or "").lower() not in {
                    "toolcall",
                    "tool_call",
                }:
                    continue
                call_id = str(item.get("id") or item.get("toolCallId") or "").strip()
                if not call_id:
                    call_id = f"transcript-line-{line_number}-call-{item_index}"
                pending.append(
                    ToolEvent(
                        tool_call_id=call_id,
                        tool_name=str(item.get("name") or ""),
                        arguments=item.get("arguments"),
                        call_line=line_number,
                        call_timestamp_ms=_timestamp_ms(payload, message),
                    )
                )
        elif role in {"toolresult", "tool_result"}:
            call_id = str(message.get("toolCallId") or message.get("tool_call_id") or "").strip()
            result_order.append((line_number, message))
            if call_id:
                results[call_id] = (line_number, _timestamp_ms(payload, message), message)

    events: list[ToolEvent] = []
    matched_result_ids: set[str] = set()
    matched_standalone_lines: set[int] = set()
    for event in pending:
        result_entry = results.get(event.tool_call_id)
        if result_entry is None:
            normalized_name = event.tool_name.strip().lower()
            fallback = next(
                (
                    (line_number, result)
                    for line_number, result in result_order
                    if line_number > event.call_line
                    and line_number not in matched_standalone_lines
                    and not str(result.get("toolCallId") or result.get("tool_call_id") or "").strip()
                    and (
                        not normalized_name
                        or str(result.get("toolName") or "").strip().lower() == normalized_name
                    )
                ),
                None,
            )
            if fallback is not None:
                line_number, result = fallback
                matched_standalone_lines.add(line_number)
                result_entry = (line_number, None, result)
        if result_entry is None:
            events.append(event)
            continue
        result_line, result_timestamp_ms, result = result_entry
        matched_result_ids.add(event.tool_call_id)
        events.append(
            ToolEvent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                arguments=event.arguments,
                call_line=event.call_line,
                call_timestamp_ms=event.call_timestamp_ms,
                result_line=result_line,
                result_timestamp_ms=result_timestamp_ms,
                result=result,
            )
        )
    standalone = [
        (line_number, result)
        for line_number, result in result_order
        if line_number not in matched_standalone_lines
        if str(result.get("toolCallId") or result.get("tool_call_id") or "").strip()
        not in matched_result_ids
    ]
    return events, standalone


def tool_result_text(message: Mapping[str, Any] | None) -> str:
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, Mapping) and str(item.get("type") or "").lower() == "text"
    )


def result_exit_code(message: Mapping[str, Any] | None) -> int | None:
    if not isinstance(message, Mapping):
        return None
    details = message.get("details")
    details = details if isinstance(details, Mapping) else {}
    exit_code = details.get("exitCode", details.get("exit_code"))
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        return exit_code
    text = tool_result_text(message)
    for pattern in _EXIT_CODE_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


def canonical_tool_status(message: Mapping[str, Any] | None) -> tuple[str, str]:
    if message is None:
        return "incomplete", "missing_result"
    text = tool_result_text(message)
    lowered = text.lower()
    details = message.get("details")
    details = details if isinstance(details, Mapping) else {}
    structured_status = str(details.get("status") or "").strip().lower()
    if structured_status == "blocked" or any(marker in lowered for marker in _BLOCKED_MARKERS):
        return "blocked", "guard_blocked"
    if structured_status in {"timeout", "timed_out"}:
        return "timeout", "tool_timeout"
    if structured_status in {"cancelled", "canceled", "killed"}:
        return "cancelled", "tool_cancelled"
    exit_code = result_exit_code(message)
    if message.get("isError") is True or (exit_code is not None and exit_code != 0):
        return "failure", "nonzero_exit" if exit_code not in (None, 0) else "tool_error"
    if structured_status in {"failed", "failure", "error"}:
        return "failure", "structured_failure"
    if structured_status in {"running", "pending"}:
        return "incomplete", "background_pending"
    tool_name = str(message.get("toolName") or "").strip().lower()
    legacy_error = any(
        marker in lowered
        for marker in (
            '"status": "error"',
            "'status': 'error'",
            "process exited with code 1",
            "process exited with code 2",
            "command exited with code 1",
            "command exited with code 2",
            "web fetch failed",
            "exec preflight:",
        )
    )
    if tool_name == "read" and any(marker in lowered for marker in ("enoent", "no such file or directory")):
        legacy_error = True
    if "usage:" in lowered and (tool_name == "exec" or "required" in lowered):
        legacy_error = True
    if legacy_error:
        return "failure", "legacy_text_error"
    return "success", ""


def operation_outcome(message: Mapping[str, Any] | None) -> str:
    status, _ = canonical_tool_status(message)
    return {
        "success": "succeeded",
        "failure": "failed",
        "timeout": "failed",
        "cancelled": "failed",
        "incomplete": "unknown",
    }.get(status, status)


def background_session_id(event: ToolEvent) -> str:
    match = _BACKGROUND_SESSION_RE.search(tool_result_text(event.result))
    return match.group(1) if match else ""


def is_terminal_process_event(event: ToolEvent) -> bool:
    arguments = event.arguments if isinstance(event.arguments, Mapping) else {}
    action = str(arguments.get("action") or "").strip().lower()
    if action in {"kill", "remove"}:
        return True
    status, _ = canonical_tool_status(event.result)
    return status != "incomplete"


def redact_observability_text(
    value: Any,
    *,
    path_replacements: Mapping[str, str] | None = None,
    limit: int | None = None,
) -> str:
    text = str(value or "")
    for source, replacement in sorted(
        (path_replacements or {}).items(), key=lambda item: len(item[0]), reverse=True
    ):
        if source:
            text = text.replace(source, replacement)
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>:<redacted>@", text)
    text = _SECRET_OPTION_RE.sub(r"\1=<redacted>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(r"\1\2<redacted>", text)
    return text if limit is None else text[:limit]


def raw_text_sha256(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
