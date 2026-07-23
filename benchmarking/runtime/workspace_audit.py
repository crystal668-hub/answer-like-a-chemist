from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from benchmarking.runtime.workspace_policy import WorkspaceAccessPolicy, _path_is_within


@dataclass(frozen=True)
class PathCandidate:
    raw_token: str
    expanded_token: str
    resolved_path: Path
    source: str


@dataclass(frozen=True)
class AuditParserCondition(Exception):
    code: str
    details: Mapping[str, Any]


@dataclass(frozen=True)
class AuditProjection:
    text: str
    recovery_code: str | None = None
    recovery_version: int | None = None


@dataclass(frozen=True)
class ToolEvent:
    tool_call_id: str
    tool_name: str
    arguments: Any
    call_line: int
    result_line: int | None = None
    result: Mapping[str, Any] | None = None


def _audit_recovery_candidates(runner_meta: Mapping[str, Any]) -> tuple[str, ...]:
    candidates: list[str] = []
    session = runner_meta.get("session_isolation")
    isolation = runner_meta.get("workspace_isolation")
    for payload in (session, isolation, runner_meta):
        if not isinstance(payload, Mapping):
            continue
        for key in (
            "archived_session_file",
            "archive_transcript_path",
            "archived_transcript_path",
            "transcript_archive_path",
        ):
            value = str(payload.get(key) or "").strip()
            if value and value not in candidates:
                candidates.append(value)
    return tuple(candidates)


def _select_audit_transcript(
    requested_path: str,
    recovery_candidates: tuple[str, ...],
) -> tuple[Path | None, dict[str, Any]]:
    candidates = tuple(value for value in (requested_path, *recovery_candidates) if value)
    for index, candidate_text in enumerate(candidates):
        candidate = Path(candidate_text).expanduser()
        if candidate.is_symlink() or not candidate.is_file():
            continue
        recovered = index > 0 or (bool(requested_path) and candidate_text != requested_path)
        return candidate, {
            "attempted": recovered,
            "succeeded": recovered,
            "source": "archive" if recovered else "active_session",
            "transcript_path": _redact_text(str(candidate)),
            "model_reinvoked": False,
        }
    return None, {
        "attempted": True,
        "succeeded": False,
        "source": "none",
        "candidate_count": len(candidates),
        "model_reinvoked": False,
    }


def _tool_events_from_transcript(
    payloads: list[tuple[int, Any]],
) -> tuple[list[ToolEvent], list[tuple[int, Mapping[str, Any]]]]:
    pending: list[ToolEvent] = []
    results: dict[str, tuple[int, Mapping[str, Any]]] = {}
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
                    )
                )
        elif role in {"toolresult", "tool_result"}:
            call_id = str(message.get("toolCallId") or message.get("tool_call_id") or "").strip()
            result_order.append((line_number, message))
            if call_id:
                results[call_id] = (line_number, message)

    events: list[ToolEvent] = []
    matched_result_ids: set[str] = set()
    for event in pending:
        result_entry = results.get(event.tool_call_id)
        if result_entry is None:
            events.append(event)
            continue
        result_line, result = result_entry
        matched_result_ids.add(event.tool_call_id)
        events.append(
            ToolEvent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                arguments=event.arguments,
                call_line=event.call_line,
                result_line=result_line,
                result=result,
            )
        )
    standalone = [
        (line_number, result)
        for line_number, result in result_order
        if str(result.get("toolCallId") or result.get("tool_call_id") or "").strip() not in matched_result_ids
    ]
    return events, standalone


def _tool_result_text(message: Mapping[str, Any] | None) -> str:
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


def _operation_outcome(message: Mapping[str, Any] | None) -> str:
    if message is None:
        return "unknown"
    text = _tool_result_text(message)
    if "benchmark_workspace_guard_blocked" in text or "benchmark_workdir_invalid" in text:
        return "blocked"
    if message.get("isError") is True:
        return "failed"
    details = message.get("details")
    details = details if isinstance(details, Mapping) else {}
    exit_code = details.get("exitCode", details.get("exit_code"))
    if isinstance(exit_code, int) and exit_code != 0:
        return "failed"
    match = re.search(r"Command exited with code\s+(-?\d+)", text)
    if match and int(match.group(1)) != 0:
        return "failed"
    return "succeeded"


def _workdir_fallback_finding(
    message: Any,
    *,
    line_number: int | None,
    policy: WorkspaceAccessPolicy,
    tool_call_id: str = "",
    operation_outcome: str | None = None,
    call_line: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(message, Mapping) or str(message.get("role") or "").lower() not in {
        "toolresult",
        "tool_result",
    }:
        return None
    tool_name = str(message.get("toolName") or "").strip().lower()
    if tool_name not in {"exec", "execute", "shell", "bash", "command"}:
        return None
    text = _tool_result_text(message)
    match = re.search(r'Warning: workdir "([^"]+)" is unavailable; using "([^"]+)"\.', text)
    if match:
        fallback_path = Path(match.group(2)).expanduser().resolve(strict=False)
        fallback_allowed = policy.allows("workdir", fallback_path)
        outcome = operation_outcome or _operation_outcome(message)
        possible_exposure = not fallback_allowed and outcome in {"succeeded", "unknown"}
        return {
            "rule_id": "workdir_fallback",
            "tool_call_id": tool_call_id
            or str(message.get("toolCallId") or message.get("tool_call_id") or f"result-line-{line_number}"),
            "tool_name": tool_name,
            "candidate_source": "tool_result.warning",
            "access_mode": "workdir",
            "operation_outcome": outcome,
            "command_excerpt": _redact_text(text),
            "requested_workdir": _redact_text(match.group(1)),
            "fallback_workdir": _redact_text(str(fallback_path)),
            "fallback_allowed": fallback_allowed,
            "resource_provenance": "unknown",
            "information_exposure": "possible" if possible_exposure else "none",
            "boundary_effect": "warning" if fallback_allowed else "violated",
            "evidence": {
                "call_line": call_line,
                "result_line": line_number,
                "result_is_error": bool(message.get("isError", False)),
                "exit_code": None,
                "result_excerpt": _redact_text(text, limit=240),
            },
        }
    return None


_ENVIRONMENT_REFERENCE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_UNSUPPORTED_DYNAMIC_EXPRESSION = re.compile(r"\$\(|`|\$\{[^}]*[^A-Za-z0-9_][^}]*\}")


def _expand_path_expression(token: str, environment: Mapping[str, str]) -> str | None:
    expanded = str(token)
    if _UNSUPPORTED_DYNAMIC_EXPRESSION.search(expanded):
        return None
    if expanded == "~" or expanded.startswith("~/"):
        home = str(environment.get("HOME") or "").strip()
        if not home:
            return None
        expanded = home if expanded == "~" else home.rstrip("/") + expanded[1:]
    elif expanded.startswith("~"):
        return None
    if "$" in _ENVIRONMENT_REFERENCE.sub("", expanded):
        return None

    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        name = match.group(1) or match.group(2) or ""
        if name not in environment:
            missing = True
            return match.group(0)
        return str(environment[name])

    expanded = _ENVIRONMENT_REFERENCE.sub(replace, expanded)
    if missing:
        return None
    return expanded


def _redact_text(text: str, *, limit: int = 1000) -> str:
    redacted = re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|password|secret|token)(\s*[=:]\s*|\s+)([^\s,;]+)",
        r"\1\2***",
        str(text or ""),
    )
    return redacted[:limit]


_EXEC_TOOLS = frozenset({"exec", "execute", "shell", "bash", "command"})
_PATH_ARGUMENT_KEYS = frozenset(
    {
        "path",
        "file_path",
        "directory",
        "workdir",
        "cwd",
        "source",
        "target",
    }
)


def _resolve_candidate(
    raw_token: str,
    *,
    source: str,
    base_dir: Path | None,
    environment: Mapping[str, str],
    require_path_syntax: bool,
) -> PathCandidate | None:
    token = str(raw_token).strip().strip(",")
    if not token or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", token):
        return None
    expanded = _expand_path_expression(token, environment)
    if expanded is None or not expanded:
        return None
    if require_path_syntax and not (
        expanded.startswith(("/", "./", "../", "~/"))
        or "/" in expanded
        or any(part == ".." for part in Path(expanded).parts)
    ):
        return None
    path = Path(expanded)
    if not path.is_absolute():
        if base_dir is None:
            return None
        path = base_dir / path
    return PathCandidate(
        raw_token=token,
        expanded_token=expanded,
        resolved_path=path.expanduser().resolve(strict=False),
        source=source,
    )


def _without_dynamic_command_substitutions(command: str) -> str:
    result: list[str] = []
    index = 0
    quote = ""
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'" and index + 1 < len(command):
            result.extend((char, command[index + 1]))
            index += 2
            continue
        if char in {"'", '"'}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            result.append(char)
            index += 1
            continue
        if quote != "'" and command.startswith("$(", index):
            depth = 1
            index += 2
            while index < len(command) and depth:
                if command.startswith("$(", index):
                    depth += 1
                    index += 2
                    continue
                if command[index] == ")":
                    depth -= 1
                index += 1
            result.append(" ")
            continue
        if quote != "'" and char == "`":
            index += 1
            while index < len(command):
                if command[index] == "\\" and index + 1 < len(command):
                    index += 2
                    continue
                if command[index] == "`":
                    index += 1
                    break
                index += 1
            result.append(" ")
            continue
        result.append(char)
        index += 1
    return "".join(result)


_HEREDOC_BODY_START = "\x00OPENCLAW_AUDIT_HEREDOC_BODY_START\x00"
_HEREDOC_BODY_END = "\x00OPENCLAW_AUDIT_HEREDOC_BODY_END\x00"
_HEREDOC_BODY_UNSAFE = re.compile(r"[^A-Za-z0-9_./~${}=:+@%!-]+")
_COMMAND_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
_SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_UNTERMINATED_HEREDOC_CODE = "exec_unterminated_heredoc_eof"
_UNTERMINATED_HEREDOC_RECOVERY_VERSION = 1


def _heredoc_declarations(line: str) -> list[tuple[str, bool]]:
    declarations: list[tuple[str, bool]] = []
    index = 0
    quote = ""
    while index < len(line):
        char = line[index]
        if char == "\\" and quote != "'" and index + 1 < len(line):
            index += 2
            continue
        if char in {"'", '"'}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
            index += 1
            continue
        if quote:
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            break
        if line.startswith("<<<", index):
            index += 3
            continue
        if not line.startswith("<<", index):
            index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in {" ", "\t"}:
            cursor += 1

        delimiter: list[str] = []
        delimiter_quote = ""
        word_started = False
        while cursor < len(line):
            char = line[cursor]
            if not delimiter_quote and (char.isspace() or char in ";|&()<>"):
                break
            if char == "\\" and delimiter_quote != "'" and cursor + 1 < len(line):
                word_started = True
                delimiter.append(line[cursor + 1])
                cursor += 2
                continue
            if char in {"'", '"'}:
                word_started = True
                if not delimiter_quote:
                    delimiter_quote = char
                elif delimiter_quote == char:
                    delimiter_quote = ""
                else:
                    delimiter.append(char)
                cursor += 1
                continue
            word_started = True
            delimiter.append(char)
            cursor += 1
        if delimiter_quote:
            raise ValueError("No closing quotation in here-document delimiter")
        if not word_started:
            raise ValueError("Missing here-document delimiter")
        declarations.append(("".join(delimiter), strip_tabs))
        index = cursor
    return declarations


def _build_command_audit_projection(command: str) -> AuditProjection:
    projected: list[str] = []
    pending: list[tuple[str, bool]] = []
    body_open = False
    for line in command.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        if pending:
            delimiter, strip_tabs = pending[0]
            candidate = content.lstrip("\t") if strip_tabs else content
            if candidate == delimiter:
                projected.append(f" {_HEREDOC_BODY_END} ")
                pending.pop(0)
                body_open = False
                if pending:
                    projected.append(f" {_HEREDOC_BODY_START} ")
                    body_open = True
                else:
                    projected.append(" ; ")
                projected.append(newline or "\n")
                continue
            projected.append(_HEREDOC_BODY_UNSAFE.sub(" ", content))
            projected.append(newline or "\n")
            continue

        projected.append(line)
        declarations = _heredoc_declarations(content)
        if declarations:
            pending.extend(declarations)
            projected.append(f" {_HEREDOC_BODY_START} ")
            body_open = True

    if pending or body_open:
        raise AuditParserCondition(
            code=_UNTERMINATED_HEREDOC_CODE,
            details={
                "partial_projection": "".join(projected),
                "pending_delimiters": tuple(delimiter for delimiter, _ in pending),
                "body_open": body_open,
            },
        )
    return AuditProjection(text="".join(projected))


def recover_unterminated_heredoc_eof(
    command: str,
    condition: AuditParserCondition,
) -> AuditProjection:
    del command
    partial_projection = condition.details.get("partial_projection")
    pending_delimiters = condition.details.get("pending_delimiters")
    if (
        condition.code != _UNTERMINATED_HEREDOC_CODE
        or not isinstance(partial_projection, str)
        or not partial_projection
        or not isinstance(pending_delimiters, tuple)
        or not pending_delimiters
        or condition.details.get("body_open") is not True
        or _HEREDOC_BODY_START not in partial_projection
    ):
        raise ValueError("Incomplete unterminated heredoc audit projection state")
    return AuditProjection(
        text=f"{partial_projection} {_HEREDOC_BODY_END} ",
        recovery_code=condition.code,
        recovery_version=_UNTERMINATED_HEREDOC_RECOVERY_VERSION,
    )


AUDIT_ERROR_RECOVERY_HANDLERS = {
    _UNTERMINATED_HEREDOC_CODE: recover_unterminated_heredoc_eof,
}


def _command_audit_projection(command: str) -> AuditProjection:
    try:
        return _build_command_audit_projection(command)
    except AuditParserCondition as condition:
        handler = AUDIT_ERROR_RECOVERY_HANDLERS.get(condition.code)
        if handler is None:
            raise
        projection = handler(command, condition)
        if (
            not isinstance(projection, AuditProjection)
            or projection.recovery_code != condition.code
            or not isinstance(projection.recovery_version, int)
            or projection.recovery_version < 1
            or not projection.text
        ):
            raise ValueError(f"Incomplete audit recovery projection: {condition.code}")
        return projection


def _exec_tokens(command: str) -> tuple[list[str], AuditProjection]:
    projection = _command_audit_projection(command)
    lexer = shlex.shlex(
        _without_dynamic_command_substitutions(projection.text),
        posix=True,
        punctuation_chars="|&;()<>",
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer), projection


def _exec_command_candidates(
    command: str,
    *,
    workspace: Path,
    environment: Mapping[str, str],
) -> tuple[list[PathCandidate], AuditProjection]:
    candidates: list[PathCandidate] = []
    current_dir: Path | None = workspace
    command_name = ""
    pending_cd: Path | None = None
    cd_target_seen = False
    in_heredoc = False

    tokens, projection = _exec_tokens(command)
    for raw_token in tokens:
        token = raw_token.strip()
        if not token:
            continue
        if token == _HEREDOC_BODY_START:
            in_heredoc = True
            continue
        if token == _HEREDOC_BODY_END:
            in_heredoc = False
            continue
        if not in_heredoc and token in _COMMAND_SEPARATORS:
            if command_name == "cd":
                if token in {"&&", ";"}:
                    if cd_target_seen:
                        current_dir = pending_cd
                    else:
                        home = str(environment.get("HOME") or "").strip()
                        current_dir = Path(home).expanduser().resolve(strict=False) if home else None
                else:
                    current_dir = None
            command_name = ""
            pending_cd = None
            cd_target_seen = False
            continue
        if not in_heredoc and token in {"(", ")"}:
            current_dir = None
            command_name = ""
            pending_cd = None
            cd_target_seen = False
            continue

        path_token = token.rsplit("=", 1)[1] if "=" in token else token
        source = "exec.heredoc" if in_heredoc else "exec.command"
        candidate = _resolve_candidate(
            path_token,
            source=source,
            base_dir=current_dir,
            environment=environment,
            require_path_syntax=True,
        )
        if candidate is not None:
            candidates.append(candidate)

        if in_heredoc:
            continue
        if not command_name:
            if _SHELL_ASSIGNMENT.match(token):
                continue
            command_name = token
            continue
        if command_name != "cd" or cd_target_seen or token == "--":
            continue
        cd_target_seen = True
        if token == "-" or token.startswith("-"):
            pending_cd = None
            continue
        cd_candidate = _resolve_candidate(
            token,
            source="exec.command",
            base_dir=current_dir,
            environment=environment,
            require_path_syntax=False,
        )
        pending_cd = cd_candidate.resolved_path if cd_candidate is not None else None
        if candidate is None and cd_candidate is not None:
            candidates.append(cd_candidate)
    return candidates, projection


def _candidate_paths(
    tool_name: str,
    arguments: Any,
    *,
    workspace: Path,
    environment: Mapping[str, str],
) -> tuple[list[PathCandidate], AuditProjection | None]:
    normalized_tool = str(tool_name or "").strip().lower()
    candidates: list[PathCandidate] = []
    if normalized_tool in _EXEC_TOOLS:
        if isinstance(arguments, str):
            command = arguments
            explicit_arguments: Mapping[str, Any] = {}
        elif isinstance(arguments, Mapping):
            command = str(arguments.get("command") or "")
            explicit_arguments = arguments
        else:
            return [], None
        command_candidates, projection = _exec_command_candidates(
            command,
            workspace=workspace,
            environment=environment,
        )
        candidates.extend(command_candidates)
        for key in ("workdir", "cwd"):
            value = explicit_arguments.get(key)
            if isinstance(value, (str, os.PathLike)):
                candidate = _resolve_candidate(
                    str(value),
                    source=f"exec.{key}",
                    base_dir=workspace,
                    environment=environment,
                    require_path_syntax=False,
                )
                if candidate is not None:
                    candidates.append(candidate)
        return candidates, projection

    if not isinstance(arguments, Mapping):
        return [], None
    for key, value in arguments.items():
        normalized_key = str(key).strip().lower()
        if normalized_key not in _PATH_ARGUMENT_KEYS or not isinstance(value, (str, os.PathLike)):
            continue
        candidate = _resolve_candidate(
            str(value),
            source=f"{normalized_tool or 'tool'}.{normalized_key}",
            base_dir=workspace,
            environment=environment,
            require_path_syntax=False,
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates, None


def _command_excerpt(tool_name: str, arguments: Any) -> str:
    normalized_tool = str(tool_name or "").strip().lower()
    if isinstance(arguments, str):
        return _redact_text(arguments)
    if not isinstance(arguments, Mapping):
        return ""
    if normalized_tool in _EXEC_TOOLS:
        payload = {key: arguments[key] for key in ("command", "workdir", "cwd") if key in arguments}
    else:
        payload = {key: value for key, value in arguments.items() if str(key).strip().lower() in _PATH_ARGUMENT_KEYS}
    return _redact_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _transcript_audit_failure(
    exc: Exception,
    *,
    line_number: int | None = None,
    tool_name: str = "",
    arguments: Any = None,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "rule_id": "transcript_audit_failed",
        "tool_name": str(tool_name or "").strip().lower(),
        "command_excerpt": _command_excerpt(tool_name, arguments) if tool_name else "",
        "exception_type": type(exc).__name__,
    }
    message = _redact_text(str(exc), limit=240).strip()
    if message:
        finding["exception_message"] = message
    if line_number is not None:
        finding["transcript_line"] = line_number
    return finding


_READ_TOOLS = frozenset({"read", "read_file", "image", "open_file"})
_LIST_TOOLS = frozenset({"list", "list_directory", "ls"})
_SEARCH_TOOLS = frozenset({"search", "find", "glob"})
_WRITE_TOOLS = frozenset({"write", "write_file", "create", "create_file", "save"})
_MUTATE_TOOLS = frozenset({"edit", "delete", "remove", "move", "rename", "copy"})
_EXECUTE_TOOLS = frozenset({"execute_file", "run_file"})


def _access_mode_for_candidate(tool_name: str, candidate: PathCandidate) -> str:
    normalized_tool = str(tool_name or "").strip().lower()
    source_key = candidate.source.rsplit(".", 1)[-1]
    if candidate.source in {"exec.workdir", "exec.cwd"}:
        return "workdir"
    if normalized_tool in _EXEC_TOOLS:
        return "unknown"
    if normalized_tool in _READ_TOOLS:
        return "read"
    if normalized_tool in _LIST_TOOLS:
        return "list"
    if normalized_tool in _SEARCH_TOOLS:
        return "search"
    if normalized_tool in _WRITE_TOOLS:
        return "write"
    if normalized_tool in _MUTATE_TOOLS:
        if normalized_tool == "copy" and source_key == "source":
            return "read"
        return "mutate"
    if normalized_tool in _EXECUTE_TOOLS:
        return "execute"
    return "unknown"


def _failed_result_contains_external_content(message: Mapping[str, Any] | None) -> bool:
    text = _tool_result_text(message).strip()
    if not text:
        return False
    lowered = text.lower()
    if "benchmark_workspace_guard_blocked" in lowered or "benchmark_workdir_invalid" in lowered:
        return False
    error_markers = (
        "enoent",
        "no such file",
        "permission denied",
        "access denied",
        "not found",
        '"status": "error"',
        "command exited with code",
    )
    return not any(marker in lowered for marker in error_markers)


def _information_exposure(
    *,
    access_mode: str,
    operation_outcome: str,
    result: Mapping[str, Any] | None,
) -> str:
    if access_mode in {"write", "mutate"}:
        return "none"
    if access_mode == "workdir":
        return "possible" if operation_outcome in {"succeeded", "unknown"} else "none"
    if access_mode in {"read", "list", "search", "execute"}:
        if operation_outcome == "succeeded":
            return "confirmed"
        if operation_outcome == "unknown":
            return "possible"
        return "confirmed" if _failed_result_contains_external_content(result) else "none"
    if operation_outcome in {"succeeded", "unknown"}:
        return "possible"
    return "confirmed" if _failed_result_contains_external_content(result) else "none"


def _result_exit_code(message: Mapping[str, Any] | None) -> int | None:
    if not isinstance(message, Mapping):
        return None
    details = message.get("details")
    details = details if isinstance(details, Mapping) else {}
    value = details.get("exitCode", details.get("exit_code"))
    if isinstance(value, int):
        return value
    match = re.search(r"Command exited with code\s+(-?\d+)", _tool_result_text(message))
    return int(match.group(1)) if match else None


def _forbidden_access_findings(
    *,
    event: ToolEvent,
    workspace: Path,
    policy: WorkspaceAccessPolicy,
    environment: Mapping[str, str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    candidates, projection = _candidate_paths(
        event.tool_name,
        event.arguments,
        workspace=workspace,
        environment=environment,
    )
    outcome = _operation_outcome(event.result)
    if projection is not None and projection.recovery_code is not None:
        recovery_handler = AUDIT_ERROR_RECOVERY_HANDLERS.get(projection.recovery_code)
        findings.append(
            {
                "rule_id": "transcript_audit_recovered",
                "audit_error_code": projection.recovery_code,
                "recovery_handler": getattr(recovery_handler, "__name__", ""),
                "recovery_version": projection.recovery_version,
                "tool_call_id": event.tool_call_id,
                "tool_name": str(event.tool_name or "").strip().lower(),
                "candidate_source": "transcript.tool_call",
                "access_mode": "unknown",
                "operation_outcome": outcome,
                "resource_provenance": "unknown",
                "information_exposure": "none",
                "boundary_effect": "warning",
                "command_excerpt": _command_excerpt(event.tool_name, event.arguments),
                "evidence": {
                    "call_line": event.call_line,
                    "result_line": event.result_line,
                    "result_is_error": bool(event.result and event.result.get("isError") is True),
                    "exit_code": _result_exit_code(event.result),
                },
            }
        )
    for candidate in candidates:
        access_mode = _access_mode_for_candidate(event.tool_name, candidate)
        if policy.allows(access_mode, candidate.resolved_path):
            continue
        matches = [
            root
            for root in policy.protected_roots
            if _path_is_within(candidate.resolved_path, root.path)
        ]
        if not matches:
            continue
        matched = min(matches, key=lambda root: (-len(root.path.parts), root.policy_id))
        finding_key = (str(candidate.resolved_path), matched.policy_id)
        if finding_key in seen:
            continue
        seen.add(finding_key)
        findings.append(
            {
                "rule_id": "protected_path_access",
                "tool_call_id": event.tool_call_id,
                "policy_id": matched.policy_id,
                "tool_name": str(event.tool_name or "").strip().lower(),
                "candidate_source": candidate.source,
                "access_mode": access_mode,
                "operation_outcome": outcome,
                "resolved_path": str(candidate.resolved_path),
                "matched_root": str(matched.path),
                "resource_provenance": "unknown",
                "information_exposure": _information_exposure(
                    access_mode=access_mode,
                    operation_outcome=outcome,
                    result=event.result,
                ),
                "boundary_effect": "violated",
                "command_excerpt": _command_excerpt(event.tool_name, event.arguments),
                "evidence": {
                    "call_line": event.call_line,
                    "result_line": event.result_line,
                    "result_is_error": bool(event.result and event.result.get("isError") is True),
                    "exit_code": _result_exit_code(event.result),
                    "result_excerpt": _redact_text(_tool_result_text(event.result), limit=240),
                },
            }
        )
    return findings
