from __future__ import annotations

import json

import pytest

from benchmarking.runtime.error_capture import capture_execution_error

QWEN_ACCESS_DENIED = "403 Access to model denied. Please make sure you are eligible for using the model."


def test_captures_qwen_access_denied_after_long_diagnostics() -> None:
    stderr = "\n".join(
        (
            "[agents/tool-policy] " + "x" * 1500,
            "[agent/embedded] embedded run agent end: runId=run-1 isError=true "
            "model=qwen3.7-max provider=qwen error=LLM request failed. "
            f"rawError={QWEN_ACCESS_DENIED}",
            "[agent/embedded] embedded run failover decision: runId=run-1 "
            "stage=assistant decision=surface_error reason=auth from=qwen/qwen3.7-max "
            f"rawError={QWEN_ACCESS_DENIED}",
        )
    )

    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=stderr,
        session_id="session-1",
    )
    details = captured.to_details()

    assert captured.code == "provider_access_denied"
    assert captured.layer == "provider_authorization"
    assert captured.retryable is False
    assert captured.message == "Access to model denied. Please make sure you are eligible for using the model."
    assert details["primary_error"] == {
        "source": "stderr",
        "line_number": 3,
        "event_kind": "provider_request_failed",
        "status_code": 403,
        "error_code": None,
        "error_type": None,
        "message": "Access to model denied. Please make sure you are eligible for using the model.",
        "raw": QWEN_ACCESS_DENIED,
        "parser": "openclaw_raw_error",
    }
    assert len(details["observed_errors"]) == 2
    assert details["stderr_excerpt"].startswith("[agents/tool-policy]")
    assert QWEN_ACCESS_DENIED not in details["stderr_excerpt"]


@pytest.mark.parametrize(
    ("raw_error", "expected_code", "expected_status", "retryable"),
    (
        ("401 Invalid API key", "provider_auth_error", 401, False),
        ("403 Forbidden", "provider_access_denied", 403, False),
        ("HTTP 408 request timeout", "provider_timeout", 408, True),
        ("HTTP 499 client closed request", "provider_timeout", 499, True),
        ("429 Too many requests", "provider_rate_limit_error", 429, False),
        ("HTTP 500 internal server error", "provider_service_error", 500, True),
        ("HTTP 502 bad gateway", "provider_service_error", 502, True),
        ("HTTP 503 service unavailable", "provider_service_error", 503, True),
        ("HTTP 504 gateway timeout", "provider_timeout", 504, True),
    ),
)
def test_maps_provider_http_status_without_losing_raw_error(
    raw_error: str,
    expected_code: str,
    expected_status: int,
    retryable: bool,
) -> None:
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=f"[agent/embedded] error=LLM request failed. rawError={raw_error}",
        session_id="session-1",
    )

    assert captured.code == expected_code
    assert captured.retryable is retryable
    assert captured.to_details()["primary_error"]["status_code"] == expected_status
    assert captured.to_details()["primary_error"]["raw"] == raw_error


def test_captures_structured_provider_error_envelope() -> None:
    raw_payload = json.dumps(
        {
            "status": 429,
            "error": {
                "code": "insufficient_quota",
                "type": "quota_error",
                "message": "Monthly quota exhausted",
            },
        },
        separators=(",", ":"),
    )

    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=f"LLM request failed. rawError={raw_payload}",
        session_id="session-1",
    )
    primary = captured.to_details()["primary_error"]

    assert captured.code == "provider_quota_error"
    assert captured.message == "Monthly quota exhausted"
    assert primary["status_code"] == 429
    assert primary["error_code"] == "insufficient_quota"
    assert primary["error_type"] == "quota_error"
    assert primary["raw"] == raw_payload


def test_does_not_treat_tool_http_403_as_provider_error() -> None:
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=(
            "[tool] web_fetch failed: HTTP 403 Forbidden\n"
            "[diagnostic] tool execution stopped before completion"
        ),
        session_id="session-1",
    )

    assert captured.code == "openclaw_subprocess_failed"
    assert "primary_error" not in captured.to_details()
    assert "observed_errors" not in captured.to_details()


def test_captures_unsupported_thinking_level_as_openclaw_config_error() -> None:
    stderr = 'Error: Thinking level "high" is not supported for minimax/MiniMax-M3. Use one of: off, adaptive.\n'

    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=stderr,
        session_id="session-1",
    )
    details = captured.to_details()

    assert captured.code == "openclaw_thinking_level_unsupported"
    assert captured.message == stderr.strip()
    assert captured.layer == "openclaw_config"
    assert captured.retryable is False
    assert details["thinking_level"] == "high"
    assert details["model"] == "minimax/MiniMax-M3"
    assert details["supported_thinking_levels"] == ["off", "adaptive"]
    assert details["primary_error"]["raw"] == stderr.strip()


def test_captures_provider_transport_error_without_http_status() -> None:
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr="Provider request failed: context deadline exceeded",
        session_id="session-1",
    )

    assert captured.code == "provider_transport_error"
    assert captured.retryable is True
    assert captured.message == "context deadline exceeded"
    assert captured.to_details()["primary_error"]["raw"] == "context deadline exceeded"


def test_stream_read_error_remains_primary_when_later_diagnostics_end_in_quotes() -> None:
    stderr = "\n".join(
        (
            "[openai-transport] [responses] error provider=openai message=stream_read_error",
            "[agent/embedded] embedded run agent end: isError=true "
            "error=LLM request timed out. rawError=stream_read_error",
            "[agent/embedded] embedded run failover decision: decision=surface_error "
            "reason=timeout rawError=stream_read_error",
            '[diagnostic] lane task error: error="FailoverError: LLM request timed out."',
            '[diagnostic] session task error: error="FailoverError: LLM request timed out."',
        )
    )

    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=stderr,
        session_id="session-1",
    )
    details = captured.to_details()

    assert captured.code == "provider_transport_error"
    assert captured.layer == "provider_transport"
    assert captured.retryable is True
    assert captured.message == "stream_read_error"
    assert details["primary_error"]["raw"] == "stream_read_error"
    assert all(item["message"] != '"' for item in details["observed_errors"])


def test_structured_provider_error_outranks_later_generic_diagnostic() -> None:
    structured = json.dumps(
        {
            "status": 503,
            "error": {
                "code": "service_unavailable",
                "type": "upstream_error",
                "message": "Provider temporarily unavailable",
            },
        },
        separators=(",", ":"),
    )
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr="\n".join(
            (
                f"LLM request failed. rawError={structured}",
                "Provider request failed: generic shutdown diagnostic",
            )
        ),
        session_id="session-1",
    )

    assert captured.code == "provider_service_error"
    assert captured.retryable is True
    assert captured.message == "Provider temporarily unavailable"
    assert captured.to_details()["primary_error"]["error_code"] == "service_unavailable"


def test_scans_stdout_when_stderr_contains_unrelated_diagnostics() -> None:
    captured = capture_execution_error(
        returncode=1,
        stdout="LLM request failed. rawError=403 Model access denied",
        stderr="[diagnostic] wrapper cleanup complete",
        session_id="session-1",
    )

    assert captured.code == "provider_access_denied"
    assert captured.source == "stdout"
    assert captured.to_details()["primary_error"]["status_code"] == 403


def test_preserves_unknown_provider_error_instead_of_generic_subprocess_message() -> None:
    raw_error = "599 Vendor routing fabric unavailable"
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=f"LLM request failed. rawError={raw_error}",
        session_id="session-1",
    )

    assert captured.code == "provider_error"
    assert captured.message == "Vendor routing fabric unavailable"
    assert captured.to_details()["primary_error"]["status_code"] == 599
    assert captured.to_details()["primary_error"]["raw"] == raw_error


def test_local_config_error_evidence_points_to_the_matching_log_line() -> None:
    matching_line = (
        "Error: failed to apply resolved secret assignment at models.providers.qwen.apiKey "
        "(Path segment does not exist at models.providers.qwen.)"
    )
    captured = capture_execution_error(
        returncode=1,
        stdout="",
        stderr=f"\nstartup diagnostic\n{matching_line}\nshutdown diagnostic",
        session_id="session-1",
    )
    primary = captured.to_details()["primary_error"]

    assert captured.code == "openclaw_config_secret_assignment_error"
    assert primary["line_number"] == 3
    assert primary["raw"] == matching_line
