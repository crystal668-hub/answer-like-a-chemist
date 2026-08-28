from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from benchmarking.core.convergence import is_timeout_family_text

SECRET_ASSIGNMENT_RE = re.compile(
    r"failed to apply resolved secret assignment at (?P<path>[A-Za-z0-9_.-]+)",
    re.I,
)
MISSING_PATH_SEGMENT_RE = re.compile(
    r"Path segment does not exist at (?P<path>[A-Za-z0-9_.-]+)",
    re.I,
)
CONFIG_LOAD_RE = re.compile(r"failed to load config|config parse|invalid config", re.I)
EXECUTABLE_MISSING_RE = re.compile(r"missing openclaw executable|command not found", re.I)
UNSUPPORTED_THINKING_LEVEL_RE = re.compile(
    r'^Error: Thinking level "(?P<level>[^"]+)" is not supported for (?P<model>[^.]+)\.'
    r"(?: Use one of: (?P<supported>[^.]+)\.)?",
    re.I | re.M,
)
RAW_ERROR_RE = re.compile(r"\brawError=(?P<raw>.+)$", re.I)
HTTP_ERROR_RE = re.compile(
    r"^(?:HTTP(?:\s+status)?\s*)?(?P<status>[1-5]\d{2})(?:\s*[:;-]?\s*)(?P<message>.*)$",
    re.I,
)
PROVIDER_HTTP_RE = re.compile(
    r"(?:provider|LLM).{0,120}?(?:request failed|request error|error).{0,80}?"
    r"(?P<raw>HTTP(?:\s+status)?\s+[1-5]\d{2}.*)$",
    re.I,
)
PROVIDER_MESSAGE_RE = re.compile(
    r"(?:provider|LLM).{0,120}?(?:request failed|request error|request timed out)"
    r"(?:[\s.:;=-]+)(?P<raw>.+)$",
    re.I,
)
DIRECT_PROVIDER_MARKERS = (
    "invalid api key",
    "auth failed",
    "unauthorized",
    "rate limit",
    "ratelimit",
    "too many requests",
    "insufficient_quota",
    "quota exceeded",
    "billing hard limit",
    "context length",
    "maximum context",
    "context overflow",
    "econnaborted",
    "etimedout",
    "esockettimedout",
    "stream_read_error",
)

ERROR_EVIDENCE_PARSER_PRIORITY = {
    "openclaw_raw_error": 40,
    "provider_http_error": 30,
    "provider_error_message": 20,
    "provider_diagnostic": 10,
}


@dataclass(frozen=True)
class ErrorEvidence:
    source: str
    line_number: int
    event_kind: str
    status_code: int | None
    error_code: str | None
    error_type: str | None
    message: str
    raw: str
    parser: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "line_number": self.line_number,
            "event_kind": self.event_kind,
            "status_code": self.status_code,
            "error_code": self.error_code,
            "error_type": self.error_type,
            "message": self.message,
            "raw": self.raw,
            "parser": self.parser,
        }


@dataclass(frozen=True)
class ExecutionErrorClassification:
    code: str
    message: str
    layer: str
    retryable: bool
    source: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_details(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "layer": self.layer,
            "retryable": self.retryable,
            "source": self.source,
            **dict(self.details),
        }


def _excerpt(value: Any, *, limit: int = 1000) -> str:
    return str(value or "")[:limit]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_meaningful_error_text(value: str) -> bool:
    return any(character.isalnum() for character in value)


def _optional_status(value: Any) -> int | None:
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _structured_error_fields(payload: Any) -> tuple[int | None, str | None, str | None, str] | None:
    if not isinstance(payload, dict):
        return None
    envelope = payload.get("error")
    error = envelope if isinstance(envelope, dict) else payload
    status = _optional_status(
        payload.get("status")
        or payload.get("status_code")
        or payload.get("statusCode")
        or error.get("status")
        or error.get("status_code")
        or error.get("statusCode")
    )
    error_code = _optional_text(error.get("code"))
    error_type = _optional_text(error.get("type"))
    message = _optional_text(error.get("message") or error.get("detail") or payload.get("message"))
    if status is None and error_code is None and error_type is None and message is None:
        return None
    return status, error_code, error_type, message or ""


def _parse_provider_raw_error(
    raw: str,
    *,
    source: str,
    line_number: int,
    parser: str,
) -> ErrorEvidence:
    raw_text = raw.strip()
    status_code: int | None = None
    error_code: str | None = None
    error_type: str | None = None
    message = raw_text
    try:
        structured = _structured_error_fields(json.loads(raw_text))
    except (json.JSONDecodeError, TypeError):
        structured = None
    if structured is not None:
        status_code, error_code, error_type, message = structured
    else:
        http_match = HTTP_ERROR_RE.match(raw_text)
        if http_match:
            status_code = int(http_match.group("status"))
            message = http_match.group("message").strip() or raw_text
    return ErrorEvidence(
        source=source,
        line_number=line_number,
        event_kind="provider_request_failed",
        status_code=status_code,
        error_code=error_code,
        error_type=error_type,
        message=message,
        raw=raw_text,
        parser=parser,
    )


def _is_tool_error_line(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in ("[tool]", "web_fetch", "web fetch", "tool execution"))


def _extract_stream_error_evidence(*, source: str, diagnostic_text: str) -> list[ErrorEvidence]:
    evidence: list[ErrorEvidence] = []
    for line_number, line in enumerate(diagnostic_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        raw_match = RAW_ERROR_RE.search(stripped)
        if raw_match and any(marker in stripped.lower() for marker in ("llm request failed", "failover decision")):
            evidence.append(
                _parse_provider_raw_error(
                    raw_match.group("raw"),
                    source=source,
                    line_number=line_number,
                    parser="openclaw_raw_error",
                )
            )
            continue
        provider_http_match = PROVIDER_HTTP_RE.search(stripped)
        if provider_http_match and not _is_tool_error_line(stripped):
            evidence.append(
                _parse_provider_raw_error(
                    provider_http_match.group("raw"),
                    source=source,
                    line_number=line_number,
                    parser="provider_http_error",
                )
            )
            continue
        provider_message_match = PROVIDER_MESSAGE_RE.search(stripped)
        if provider_message_match and not _is_tool_error_line(stripped):
            candidate = _parse_provider_raw_error(
                provider_message_match.group("raw"),
                source=source,
                line_number=line_number,
                parser="provider_error_message",
            )
            if _is_meaningful_error_text(candidate.message or candidate.raw):
                evidence.append(candidate)
            continue
        lowered = stripped.lower()
        if not _is_tool_error_line(stripped) and any(marker in lowered for marker in DIRECT_PROVIDER_MARKERS):
            evidence.append(
                _parse_provider_raw_error(
                    stripped,
                    source=source,
                    line_number=line_number,
                    parser="provider_diagnostic",
                )
            )
    return evidence


def extract_error_evidence(*, stdout: str, stderr: str) -> list[ErrorEvidence]:
    # stderr evidence is appended last so the terminal process error remains primary.
    return [
        *_extract_stream_error_evidence(source="stdout", diagnostic_text=stdout),
        *_extract_stream_error_evidence(source="stderr", diagnostic_text=stderr),
    ]


def _provider_classification(evidence: ErrorEvidence) -> tuple[str, str, bool]:
    status = evidence.status_code
    semantic_text = " ".join(
        value for value in (evidence.error_code, evidence.error_type, evidence.message) if value
    ).lower()
    if any(marker in semantic_text for marker in ("insufficient_quota", "quota exceeded", "quota exhausted", "billing")):
        return ("provider_quota_error", "provider_quota", False)
    if any(marker in semantic_text for marker in ("context length", "maximum context", "context overflow")):
        return ("provider_context_limit_error", "provider_request", False)
    if any(marker in semantic_text for marker in ("model_not_found", "model not found", "unknown model")):
        return ("provider_model_not_found", "provider_request", False)
    if status == 401 or any(marker in semantic_text for marker in ("invalid api key", "auth failed", "unauthorized")):
        return ("provider_auth_error", "provider_auth", False)
    if status == 403:
        return ("provider_access_denied", "provider_authorization", False)
    if status == 429 or any(marker in semantic_text for marker in ("rate limit", "ratelimit", "too many requests")):
        return ("provider_rate_limit_error", "provider_rate_limit", False)
    if status in (408, 499, 504):
        return ("provider_timeout", "provider_timeout", True)
    if status in (500, 502, 503):
        return ("provider_service_error", "provider_service", True)
    if "stream_read_error" in semantic_text:
        return ("provider_transport_error", "provider_transport", True)
    if status in (400, 404, 409, 413, 422) or any(
        marker in semantic_text
        for marker in ("invalid_request_error", "role ordering", "invalid role", "response_format")
    ):
        return ("provider_request_invalid", "provider_request", False)
    if is_timeout_family_text(semantic_text):
        return ("provider_transport_error", "provider_transport", True)
    return ("provider_error", "provider", False)


def _primary_error_evidence(evidence: list[ErrorEvidence]) -> ErrorEvidence:
    return max(
        evidence,
        key=lambda item: (
            bool(item.status_code or item.error_code or item.error_type),
            ERROR_EVIDENCE_PARSER_PRIORITY.get(item.parser, 0),
            item.source == "stderr",
            item.line_number,
        ),
    )


def _matched_error_evidence(
    *,
    diagnostic_text: str,
    source: str,
    match: re.Match[str],
    event_kind: str,
    parser: str,
) -> ErrorEvidence:
    line_number = diagnostic_text.count("\n", 0, match.start()) + 1
    raw_line = diagnostic_text.splitlines()[line_number - 1].strip()
    return ErrorEvidence(
        source=source,
        line_number=line_number,
        event_kind=event_kind,
        status_code=None,
        error_code=None,
        error_type=None,
        message=raw_line,
        raw=raw_line,
        parser=parser,
    )


def _details_with_evidence(
    base_details: dict[str, Any],
    evidence: list[ErrorEvidence],
) -> dict[str, Any]:
    details = dict(base_details)
    if evidence:
        details["primary_error"] = _primary_error_evidence(evidence).to_dict()
        details["observed_errors"] = [item.to_dict() for item in evidence]
    return details


def capture_execution_error(
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
    session_id: str,
) -> ExecutionErrorClassification:
    stdout_text = str(stdout or "")
    stderr_text = str(stderr or "")
    diagnostic_text = stderr_text if stderr_text.strip() else stdout_text
    source = "stderr" if stderr_text.strip() else "stdout"
    base_details: dict[str, Any] = {
        "returncode": returncode,
        "session_id": session_id,
        "stdout_excerpt": _excerpt(stdout_text),
        "stderr_excerpt": _excerpt(stderr_text),
    }
    secret_match = SECRET_ASSIGNMENT_RE.search(diagnostic_text)
    missing_path_match = MISSING_PATH_SEGMENT_RE.search(diagnostic_text)
    if secret_match:
        path = secret_match.group("path")
        details = dict(base_details)
        details["secret_assignment_path"] = path
        if missing_path_match:
            details["missing_path_segment"] = missing_path_match.group("path")
        evidence = _matched_error_evidence(
            diagnostic_text=diagnostic_text,
            source=source,
            match=secret_match,
            event_kind="openclaw_config_error",
            parser="secret_assignment_error",
        )
        return ExecutionErrorClassification(
            code="openclaw_config_secret_assignment_error",
            message=f"OpenClaw config failed while applying resolved secret assignment at `{path}`.",
            layer="openclaw_config",
            retryable=False,
            source=source,
            details=_details_with_evidence(details, [evidence]),
        )
    if missing_path_match:
        path = missing_path_match.group("path")
        details = dict(base_details)
        details["missing_path_segment"] = path
        evidence = _matched_error_evidence(
            diagnostic_text=diagnostic_text,
            source=source,
            match=missing_path_match,
            event_kind="openclaw_config_error",
            parser="missing_config_path",
        )
        return ExecutionErrorClassification(
            code="openclaw_config_missing_path",
            message=f"OpenClaw config references missing path segment `{path}`.",
            layer="openclaw_config",
            retryable=False,
            source=source,
            details=_details_with_evidence(details, [evidence]),
        )
    config_match = CONFIG_LOAD_RE.search(diagnostic_text)
    if config_match:
        evidence = _matched_error_evidence(
            diagnostic_text=diagnostic_text,
            source=source,
            match=config_match,
            event_kind="openclaw_config_error",
            parser="config_load_error",
        )
        return ExecutionErrorClassification(
            code="openclaw_config_error",
            message="OpenClaw failed while loading benchmark runtime config.",
            layer="openclaw_config",
            retryable=False,
            source=source,
            details=_details_with_evidence(base_details, [evidence]),
        )
    unsupported_thinking_match = UNSUPPORTED_THINKING_LEVEL_RE.search(diagnostic_text)
    if unsupported_thinking_match:
        evidence = _matched_error_evidence(
            diagnostic_text=diagnostic_text,
            source=source,
            match=unsupported_thinking_match,
            event_kind="openclaw_config_error",
            parser="unsupported_thinking_level",
        )
        details = dict(base_details)
        details.update(
            {
                "thinking_level": unsupported_thinking_match.group("level"),
                "model": unsupported_thinking_match.group("model"),
                "supported_thinking_levels": [
                    item.strip()
                    for item in str(unsupported_thinking_match.group("supported") or "").split(",")
                    if item.strip()
                ],
            }
        )
        return ExecutionErrorClassification(
            code="openclaw_thinking_level_unsupported",
            message=evidence.message,
            layer="openclaw_config",
            retryable=False,
            source=source,
            details=_details_with_evidence(details, [evidence]),
        )
    executable_match = EXECUTABLE_MISSING_RE.search(diagnostic_text)
    if executable_match:
        evidence = _matched_error_evidence(
            diagnostic_text=diagnostic_text,
            source=source,
            match=executable_match,
            event_kind="openclaw_startup_error",
            parser="executable_missing",
        )
        return ExecutionErrorClassification(
            code="openclaw_executable_missing",
            message="OpenClaw executable was not available to the benchmark subprocess.",
            layer="openclaw_startup",
            retryable=False,
            source=source,
            details=_details_with_evidence(base_details, [evidence]),
        )
    evidence = extract_error_evidence(stdout=stdout_text, stderr=stderr_text)
    if evidence:
        primary = _primary_error_evidence(evidence)
        code, layer, retryable = _provider_classification(primary)
        return ExecutionErrorClassification(
            code=code,
            message=primary.message or primary.raw,
            layer=layer,
            retryable=retryable,
            source=primary.source,
            details=_details_with_evidence(base_details, evidence),
        )
    return ExecutionErrorClassification(
        code="openclaw_subprocess_failed",
        message="Single-LLM OpenClaw subprocess exited before producing a benchmark answer.",
        layer="runner_subprocess",
        retryable=False,
        source=source,
        details=base_details,
    )
