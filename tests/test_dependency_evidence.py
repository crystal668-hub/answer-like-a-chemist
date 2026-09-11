import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchmarking.runtime.dependency_evidence import validate_dependency_evidence


def evidence():
    return {
        "schema_version": 2,
        "identity": {"session_id": "s"},
        "python": {
            "executable": "/attempt/venv/bin/python",
            "prefix": "/attempt/venv",
            "base_prefix": "/usr",
        },
        "venv": {"path": "/attempt/venv"},
        "pypi": {
            "freeze_returncode": 0,
            "freeze": ["demo==1.0"],
            "replay_lock": {"status": "unavailable"},
        },
        "distributions": [
            {
                "name": "demo",
                "version": "1.0",
                "record_present": True,
                "record_sha256": hashlib.sha256(b"record").hexdigest(),
                "direct_url": "",
            }
        ],
        "dependency_audit": {"status": "clear"},
        "native_tools": {},
    }


def test_missing_lock_degrades_without_rejecting_valid_inventory(tmp_path):
    result = validate_dependency_evidence(
        evidence(), identity={"session_id": "s"}, scratch=tmp_path
    )
    assert result["status"] == "partial"
    assert result["scoreable"]


def test_invalid_inventory_identity_and_forbidden_distribution_fail_closed(tmp_path):
    for mutation in (
        lambda m: m.update(distributions=[]),
        lambda m: m.update(identity={"session_id": "other"}),
        lambda m: m["distributions"][0].update(name="verifier_grounded_benchmark"),
        lambda m: m["distributions"][0].update(record_present=False),
    ):
        manifest = evidence()
        mutation(manifest)
        assert not validate_dependency_evidence(
            manifest, identity={"session_id": "s"}, scratch=tmp_path
        )["scoreable"]


@pytest.mark.parametrize(
    "failure", ["freeze", "compile", "inventory", "compile_timeout"]
)
def test_collection_preserves_failed_stage_without_fabricating_inventory(
    tmp_path, monkeypatch, failure
):
    from benchmarking.runtime import attempt_environment as module

    environment = module.AttemptPythonEnvironment(
        tmp_path / "venv",
        tmp_path / "venv/bin/python",
        tmp_path / "venv/bin/pip",
        tmp_path / "venv/bin",
        tmp_path / "cache",
        tmp_path / "tools",
        {},
        "/python",
        "2026-09-11",
    )
    monkeypatch.setattr(module, "_native_tool_fingerprints", lambda _: {})

    def run(command, **kwargs):
        stage = (
            "freeze"
            if "freeze" in command
            else "compile"
            if "compile" in command
            else "inventory"
        )
        if stage == "compile" and failure == "compile_timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if stage == "compile":
            Path(command[command.index("--output-file") + 1]).write_text("demo==1.0\n")
        output = (
            "demo==1.0"
            if stage == "freeze"
            else "invalid"
            if failure == "inventory"
            else json.dumps(
                {
                    "distributions": evidence()["distributions"],
                    "python": evidence()["python"],
                }
            )
        )
        return subprocess.CompletedProcess(
            command,
            1 if failure == stage and stage != "inventory" else 0,
            output,
            "injected",
        )

    manifest = module.collect_dependency_manifest(
        environment, identity={"session_id": "s"}, run_subprocess=run
    )
    assert manifest["identity"] == {"session_id": "s"}
    if failure == "inventory":
        assert manifest["distributions"] is None
    elif failure.startswith("compile"):
        assert manifest["pypi"]["replay_lock"]["status"] == "unavailable"
    else:
        assert manifest["pypi"]["freeze_returncode"] == 1


def test_existing_lock_digest_mismatch_is_not_diagnostic_degradation(tmp_path):
    manifest = evidence()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/replay-requirements.txt").write_text("demo==1.0\n")
    manifest["pypi"]["replay_lock"] = {"status": "generated", "sha256": "0" * 64}
    result = validate_dependency_evidence(
        manifest, identity={"session_id": "s"}, scratch=tmp_path
    )
    assert not result["scoreable"]
    assert "lock" in result["errors"]


@pytest.mark.parametrize("status", ["postinstall_removed", "postinstall_removal_failed"])
def test_remediation_never_makes_forbidden_dependency_scoreable(tmp_path, status):
    manifest = evidence()
    manifest["dependency_audit"] = {"status": status, "forbidden_distributions": ["verifier-grounded-benchmark"]}
    assert not validate_dependency_evidence(manifest, identity={"session_id": "s"}, scratch=tmp_path)["scoreable"]
