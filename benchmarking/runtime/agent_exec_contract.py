"""Contract comparison helpers for OpenClaw 9.5 embedded entrypoints."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentExecContract:
    entrypoint: str
    exit_code: int
    stdout_json: bool
    session_id: str
    session_key: str
    cleanup_status: str
    evidence_source: str
    result_contract: str
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "entrypoint": self.entrypoint,
            "exit_code": self.exit_code,
            "stdout_json": self.stdout_json,
            "session_id": self.session_id,
            "session_key": self.session_key,
            "cleanup_status": self.cleanup_status,
            "evidence_source": self.evidence_source,
            "result_contract": self.result_contract,
            "notes": list(self.notes),
        }


def inspect_agent_exec_envelope(payload: Mapping[str, Any], *, entrypoint: str = "agent exec") -> AgentExecContract:
    status = str(payload.get("status") or "")
    result_contract = "valid" if isinstance(payload.get("payloads"), list) and isinstance(payload.get("ok"), bool) else "invalid"
    return AgentExecContract(
        entrypoint=entrypoint,
        exit_code=0 if payload.get("ok") is True else 1,
        stdout_json=True,
        session_id=str(payload.get("sessionId") or ""),
        session_key=str(payload.get("sessionKey") or ""),
        cleanup_status="owned_by_exec",
        evidence_source="exec_runtime_export" if payload.get("sessionId") else "unavailable",
        result_contract=result_contract,
        notes=(f"status={status}",),
    )


def compare_entrypoint_contracts(local: Mapping[str, Any], exec_payload: Mapping[str, Any]) -> dict[str, Any]:
    local_contract = inspect_agent_exec_envelope(local, entrypoint="agent --local")
    exec_contract = inspect_agent_exec_envelope(exec_payload, entrypoint="agent exec")
    return {
        "schema_version": 1,
        "local": local_contract.to_dict(),
        "exec": exec_contract.to_dict(),
        "equivalent_result_contract": local_contract.result_contract == exec_contract.result_contract,
        "exec_recommendation": "retain_agent_local" if exec_contract.evidence_source != "exec_runtime_export" else "requires_full_matrix",
        "comparison_limits": [
            "live provider and tool behavior require a 9.5 runtime with Node engine support",
            "cleanup and trajectory equivalence require Docker acceptance evidence",
        ],
    }
