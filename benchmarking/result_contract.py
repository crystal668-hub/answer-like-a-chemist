from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AgentResultContract:
    valid: bool
    payloads: list[dict[str, Any]]
    meta: dict[str, Any]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def normalize_agent_result_payload(payload: Any) -> AgentResultContract:
    if not isinstance(payload, dict):
        return _invalid("not_object", payload, meta={})

    candidate = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    meta = candidate.get("meta") if isinstance(candidate, dict) and isinstance(candidate.get("meta"), dict) else {}
    if not isinstance(candidate, dict) or "payloads" not in candidate:
        return _invalid("missing_payloads", payload, meta=meta)

    raw_payloads = candidate.get("payloads")
    if not isinstance(raw_payloads, list):
        return _invalid("payloads_not_list", payload, meta=meta)

    normalized: list[dict[str, Any]] = []
    for item in raw_payloads:
        if not isinstance(item, dict):
            return _invalid("invalid_payload_item", payload, meta=meta)
        text = str(item.get("text") or "").strip()
        if not text:
            return _invalid("invalid_payload_item", payload, meta=meta)
        normalized_item: dict[str, Any] = {"text": text}
        for marker in ("isError", "isReasoning", "replayInvalid"):
            if isinstance(item.get(marker), bool):
                normalized_item[marker] = item[marker]
        for marker in ("errorCode", "errorMessage"):
            if isinstance(item.get(marker), str):
                normalized_item[marker] = str(item[marker])
        normalized.append(normalized_item)

    return AgentResultContract(
        valid=True,
        payloads=normalized,
        meta=dict(meta),
        diagnostics={"schema_valid": True, "payload_count": len(normalized)},
    )


def parse_agent_stdout(output: str) -> AgentResultContract:
    stripped = str(output or "").strip()
    if not stripped:
        return AgentResultContract(
            valid=False,
            payloads=[],
            meta={},
            diagnostics={"schema_valid": False, "reason": "empty_output", "parse_mode": "diagnostic_only"},
        )

    try:
        direct = json.loads(stripped)
    except json.JSONDecodeError:
        direct = None
    if direct is not None:
        result = normalize_agent_result_payload(direct)
        result.diagnostics["parse_mode"] = "direct_json"
        return result

    for candidate in _embedded_json_objects(stripped):
        result = normalize_agent_result_payload(candidate)
        if result.valid:
            result.diagnostics["parse_mode"] = "embedded_agent_result"
            return result

    return AgentResultContract(
        valid=False,
        payloads=[],
        meta={},
        diagnostics={
            "schema_valid": False,
            "reason": "no_valid_agent_result_json",
            "parse_mode": "diagnostic_only",
            "stdout_excerpt": stripped[:4000],
        },
    )


def contract_to_payload(contract: AgentResultContract) -> dict[str, Any]:
    return {
        "result": {
            "payloads": contract.payloads,
            "meta": {
                **contract.meta,
                "stdout_diagnostics": dict(contract.diagnostics),
            },
        }
    }


def _invalid(reason: str, payload: Any, *, meta: dict[str, Any]) -> AgentResultContract:
    diagnostics: dict[str, Any] = {"schema_valid": False, "reason": reason}
    if isinstance(payload, dict):
        diagnostics["invalid_stdout_payload"] = payload
    else:
        diagnostics["invalid_stdout_payload_repr"] = repr(payload)[:1000]
    return AgentResultContract(valid=False, payloads=[], meta=dict(meta), diagnostics=diagnostics)


def _embedded_json_objects(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    candidates: list[Any] = []
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(value)
    return candidates
