from benchmarking.runtime.agent_exec_contract import compare_entrypoint_contracts, inspect_agent_exec_envelope


def test_exec_contract_requires_stable_envelope_fields() -> None:
    contract = inspect_agent_exec_envelope({"ok": True, "status": "ok", "sessionId": "s", "payloads": [{"text": "x"}]})
    assert contract.result_contract == "valid"
    assert contract.evidence_source == "exec_runtime_export"


def test_compare_recommends_local_when_exec_has_no_identity() -> None:
    report = compare_entrypoint_contracts(
        {"ok": True, "status": "ok", "sessionId": "s", "payloads": []},
        {"ok": True, "status": "ok", "payloads": []},
    )
    assert report["exec_recommendation"] == "retain_agent_local"
