from __future__ import annotations

import json

from benchmarking.core.result_contract import normalize_agent_result_payload, parse_agent_stdout


def test_accepts_wrapped_result_payloads() -> None:
    payload = {"result": {"payloads": [{"text": "Reasoning\nFINAL ANSWER: 7.59"}], "meta": {"toolSummary": {"calls": 1}}}}

    result = normalize_agent_result_payload(payload)

    assert result.valid is True
    assert result.payloads == [{"text": "Reasoning\nFINAL ANSWER: 7.59"}]
    assert result.meta["toolSummary"]["calls"] == 1
    assert result.diagnostics["schema_valid"] is True


def test_accepts_unwrapped_result_payloads() -> None:
    payload = {"payloads": [{"text": "FINAL ANSWER: A"}], "meta": {}}

    result = normalize_agent_result_payload(payload)

    assert result.valid is True
    assert result.payloads == [{"text": "FINAL ANSWER: A"}]


def test_preserves_payload_error_fields() -> None:
    payload = {
        "result": {
            "payloads": [{"text": "stream_read_error", "isError": True}],
            "meta": {"stopReason": "error"},
        }
    }

    result = normalize_agent_result_payload(payload)

    assert result.valid is True
    assert result.payloads == [{"text": "stream_read_error", "isError": True}]


def test_rejects_tool_argument_object_as_answer_payload() -> None:
    payload = {"query": "benzene NMR", "count": 5, "region": "us-en", "meta": {"session_isolation": {"session_isolation_ok": True}}}

    result = normalize_agent_result_payload(payload)

    assert result.valid is False
    assert result.payloads == []
    assert result.meta == {"session_isolation": {"session_isolation_ok": True}}
    assert result.diagnostics["schema_valid"] is False
    assert result.diagnostics["reason"] == "missing_payloads"
    assert result.diagnostics["invalid_stdout_payload"]["query"] == "benzene NMR"


def test_rejects_payloads_with_non_text_items() -> None:
    payload = {"result": {"payloads": [{"tool": "exec"}, {"text": ""}], "meta": {}}}

    result = normalize_agent_result_payload(payload)

    assert result.valid is False
    assert result.payloads == []
    assert result.diagnostics["reason"] == "invalid_payload_item"


def test_parse_agent_stdout_recovers_valid_embedded_agent_result_only() -> None:
    stdout = "warning before json\n" + json.dumps({"result": {"payloads": [{"text": "FINAL ANSWER: B"}], "meta": {}}})

    result = parse_agent_stdout(stdout)

    assert result.valid is True
    assert result.payloads == [{"text": "FINAL ANSWER: B"}]
    assert result.diagnostics["parse_mode"] == "embedded_agent_result"


def test_parse_agent_stdout_does_not_promote_embedded_tool_json() -> None:
    stdout = "tool event\n" + json.dumps({"query": "bad candidate", "count": 3})

    result = parse_agent_stdout(stdout)

    assert result.valid is False
    assert result.payloads == []
    assert result.diagnostics["schema_valid"] is False
    assert result.diagnostics["parse_mode"] == "diagnostic_only"
