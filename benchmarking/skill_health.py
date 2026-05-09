from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


RunSubprocess = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class HealthRequirement:
    skill: str
    python_modules: tuple[str, ...] = ()
    executables: tuple[str, ...] = ()
    api_keys: tuple[str, ...] = ()
    data_files: tuple[str, ...] = ()
    network_urls: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


REQUIREMENT_OVERRIDES: dict[str, HealthRequirement] = {
    "rdkit": HealthRequirement(skill="rdkit", python_modules=("rdkit",), data_files=("skills/rdkit/SKILL.md",)),
    "chem-calculator": HealthRequirement(skill="chem-calculator", python_modules=("sympy",), data_files=("skills/chem-calculator/SKILL.md",)),
    "paper-retrieval": HealthRequirement(
        skill="paper-retrieval",
        python_modules=("requests",),
        data_files=("skills/paper-retrieval/SKILL.md",),
        network_urls=("https://api.openalex.org/works?per-page=1", "https://api.crossref.org/works?rows=0"),
    ),
    "paper-access": HealthRequirement(
        skill="paper-access",
        python_modules=("requests", "bs4"),
        data_files=("skills/paper-access/SKILL.md",),
        network_urls=("https://api.crossref.org/works?rows=0",),
    ),
    "paper-parse": HealthRequirement(
        skill="paper-parse",
        python_modules=("fitz",),
        executables=("pdfinfo",),
        data_files=("skills/paper-parse/SKILL.md",),
    ),
    "paper-rerank": HealthRequirement(
        skill="paper-rerank",
        python_modules=("requests",),
        api_keys=("SU8_API_KEY",),
        data_files=("skills/paper-rerank/SKILL.md",),
    ),
    "pubchem": HealthRequirement(
        skill="pubchem",
        python_modules=("requests",),
        data_files=("skills/pubchem/SKILL.md",),
        network_urls=("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/water/property/MolecularFormula/JSON",),
    ),
    "materials-project": HealthRequirement(
        skill="materials-project",
        python_modules=("mp_api",),
        api_keys=("MP_API_KEY",),
        data_files=("skills/materials-project/SKILL.md",),
    ),
    "pymatgen": HealthRequirement(skill="pymatgen", python_modules=("pymatgen",), data_files=("skills/pymatgen/SKILL.md",)),
    "ase": HealthRequirement(skill="ase", python_modules=("ase",), data_files=("skills/ase/SKILL.md",)),
    "cclib": HealthRequirement(skill="cclib", python_modules=("cclib",), data_files=("skills/cclib/SKILL.md",)),
    "chembl-database": HealthRequirement(
        skill="chembl-database",
        python_modules=("chembl_webresource_client",),
        data_files=("skills/chembl-database/SKILL.md",),
        network_urls=("https://www.ebi.ac.uk/chembl/api/data/status.json",),
    ),
}


def health_requirements_for_allowlist(allowlist: Iterable[str]) -> dict[str, HealthRequirement]:
    requirements: dict[str, HealthRequirement] = {}
    for skill in allowlist:
        key = str(skill)
        requirements[key] = REQUIREMENT_OVERRIDES.get(
            key,
            HealthRequirement(skill=key, data_files=(f"skills/{key}/SKILL.md",)),
        )
    return requirements


def check_skill_health(
    requirement: HealthRequirement,
    *,
    workspace_root: Path,
    env: dict[str, str] | None = None,
    run_subprocess: RunSubprocess = subprocess.run,
    network_timeout_seconds: int = 3,
) -> dict[str, Any]:
    environment = os.environ.copy()
    if env is not None:
        environment = dict(env)
    checks: dict[str, Any] = {"python_modules": {}, "executables": {}, "api_keys": {}, "data_files": {}, "network": {}}
    unavailable: list[dict[str, str]] = []

    for module in requirement.python_modules:
        command = ["uv", "run", "python", "-c", f"import {module}"]
        completed = run_subprocess(
            command,
            cwd=str(workspace_root),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=network_timeout_seconds,
        )
        ok = completed.returncode == 0
        checks["python_modules"][module] = {"ok": ok, "returncode": completed.returncode}
        if not ok:
            unavailable.append({"kind": "missing_dependency", "name": module, "reason": (completed.stderr or completed.stdout).strip()[:500]})

    for executable in requirement.executables:
        ok = shutil.which(executable) is not None
        checks["executables"][executable] = {"ok": ok}
        if not ok:
            unavailable.append({"kind": "missing_executable", "name": executable, "reason": f"{executable} not found in PATH"})

    for key in requirement.api_keys:
        ok = bool(str(environment.get(key) or "").strip())
        checks["api_keys"][key] = {"ok": ok}
        if not ok:
            unavailable.append({"kind": "missing_api_key", "name": key, "reason": f"{key} is not set"})

    for relative_path in requirement.data_files:
        ok = (workspace_root / relative_path).is_file()
        checks["data_files"][relative_path] = {"ok": ok}
        if not ok:
            unavailable.append({"kind": "missing_data_file", "name": relative_path, "reason": f"{relative_path} does not exist"})

    for url in requirement.network_urls:
        command = [
            "uv",
            "run",
            "python",
            "-c",
            "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3).read(64)",
            url,
        ]
        completed = run_subprocess(
            command,
            cwd=str(workspace_root),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=network_timeout_seconds + 2,
        )
        ok = completed.returncode == 0
        checks["network"][url] = {"ok": ok, "returncode": completed.returncode}
        if not ok:
            unavailable.append({"kind": "provider_failure", "name": url, "reason": (completed.stderr or completed.stdout).strip()[:500]})

    return {
        "skill": requirement.skill,
        "available": not unavailable,
        "checks": checks,
        "unavailable_reasons": unavailable,
    }


def check_all_skill_health(
    allowlist: Iterable[str],
    *,
    workspace_root: Path,
    env: dict[str, str] | None = None,
    run_subprocess: RunSubprocess = subprocess.run,
) -> dict[str, dict[str, Any]]:
    return {
        skill: check_skill_health(requirement, workspace_root=workspace_root, env=env, run_subprocess=run_subprocess)
        for skill, requirement in health_requirements_for_allowlist(allowlist).items()
    }


def summarize_skill_health(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    available = sorted(skill for skill, report in reports.items() if report.get("available") is True)
    unavailable = sorted(skill for skill, report in reports.items() if report.get("available") is not True)
    return {
        "available_skill_count": len(available),
        "unavailable_skill_count": len(unavailable),
        "available_skills": available,
        "unavailable_skills": unavailable,
    }
