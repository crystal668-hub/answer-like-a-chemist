"""Independent validation of attempt dependency evidence and scoring eligibility."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


def validate_dependency_evidence(
    manifest: dict[str, Any],
    *,
    identity: dict[str, Any],
    scratch: Path,
    expected_venv: str | None = None,
    pypi_cutoff: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    stages: dict[str, str] = {}

    def check(stage: str, condition: bool) -> None:
        stages[stage] = "complete" if condition else "failed"
        if not condition:
            errors.append(stage)

    check("identity", manifest.get("identity") == identity)
    python = manifest.get("python") or {}
    venv = manifest.get("venv") or {}
    check(
        "interpreter",
        bool(venv.get("path"))
        and python.get("prefix") == venv.get("path")
        and python.get("prefix") != python.get("base_prefix")
        and python.get("executable") == venv.get("path", "") + "/bin/python"
        and (expected_venv is None or venv.get("path") == expected_venv),
    )
    pypi = manifest.get("pypi") or {}
    if pypi_cutoff is not None:
        check(
            "registry_policy",
            pypi.get("cutoff") == pypi_cutoff
            and pypi.get("default_index") == "https://pypi.org/simple",
        )
    frozen: dict[str, str] = {}
    freeze_ok = pypi.get("freeze_returncode") == 0 and isinstance(
        pypi.get("freeze"), list
    )
    for line in pypi.get("freeze", []) if isinstance(pypi.get("freeze"), list) else []:
        try:
            req = Requirement(line)
            specs = list(req.specifier)
            if req.url or len(specs) != 1 or specs[0].operator != "==":
                freeze_ok = False
            else:
                frozen[canonicalize_name(req.name)] = specs[0].version
        except (InvalidRequirement, TypeError):
            freeze_ok = False
    check("freeze", freeze_ok)
    rows = manifest.get("distributions")
    inventory: dict[str, str] = {}
    inventory_ok = isinstance(rows, list)
    records_ok = True
    policy_ok = (manifest.get("dependency_audit") or {}).get("status") == "clear"
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("name") or not row.get("version"):
            inventory_ok = False
            continue
        name = canonicalize_name(row["name"])
        if name in inventory:
            inventory_ok = False
        inventory[name] = row["version"]
        records_ok &= row.get("record_present") is True and bool(
            re.fullmatch(r"[a-f0-9]{64}", str(row.get("record_sha256", "")))
        )
        policy_ok &= name != "verifier-grounded-benchmark" and not row.get("direct_url")
    check("inventory", inventory_ok and inventory == frozen)
    check("record_hashes", records_ok)
    check("dependency_audit", policy_ok)
    lock = pypi.get("replay_lock") or {}
    if lock.get("status") != "generated":
        stages["lock"] = "unavailable"
        warnings.append("replay_lock_unavailable")
    else:
        path = scratch / "notes/replay-requirements.txt"
        try:
            content = path.read_bytes() if not path.is_symlink() else b""
            locked = {}
            locked_hashes = set()
            current = None
            for line in content.decode().splitlines():
                if line and not line[0].isspace() and not line.startswith("#"):
                    req = Requirement(line.rstrip(" \\"))
                    specs = list(req.specifier)
                    if req.url or len(specs) != 1 or specs[0].operator != "==":
                        raise ValueError("invalid replay requirement")
                    current = canonicalize_name(req.name)
                    if current in locked:
                        raise ValueError("duplicate replay requirement")
                    locked[current] = specs[0].version
                elif current and re.search(r"--hash=sha256:[a-f0-9]{64}(?:\s|$)", line):
                    locked_hashes.add(current)
            check(
                "lock",
                bool(content)
                and hashlib.sha256(content).hexdigest() == lock.get("sha256")
                and locked == frozen
                and locked_hashes == set(frozen),
            )
        except (OSError, UnicodeError, InvalidRequirement, ValueError):
            check("lock", False)
    native = manifest.get("native_tools") or {}
    if not native or any(
        not row.get("sha256") or not row.get("version") for row in native.values()
    ):
        warnings.append("native_tool_fingerprints_incomplete")
    return {
        "status": "invalid" if errors else "partial" if warnings else "complete",
        "scoreable": not errors,
        "errors": errors,
        "warnings": warnings,
        "stages": stages,
    }
