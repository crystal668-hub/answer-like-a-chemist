from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "pymatgen"


def _empty_payload(request: Any) -> dict[str, Any]:
    return {
        "status": "error",
        "request": request,
        "primary_result": {},
        "candidates": [],
        "diagnostics": [],
        "warnings": [],
        "errors": [],
        "tool_trace": [],
        "source_trace": [],
        "provider_health": {},
    }


def _provider_health(status: str, *, version: str | None = None, message: str | None = None) -> dict[str, Any]:
    health = {"status": status, "available": status == "available"}
    if version:
        health["version"] = version
    if message:
        health["message"] = message
    return {SKILL_NAME: health}


def _write_payload(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return result_path


def _summarize_fixture(structure: dict[str, Any]) -> dict[str, Any]:
    sites = structure.get("sites") if isinstance(structure.get("sites"), list) else []
    species = sorted({str(site.get("species")) for site in sites if isinstance(site, dict) and site.get("species")})
    return {
        "formula": structure.get("formula"),
        "space_group": structure.get("space_group"),
        "lattice": structure.get("lattice") or {},
        "site_count": len(sites),
        "species": species,
        "coordination_environments": structure.get("coordination_environments") or [],
    }


def _summarize_pymatgen_structure(structure: Any) -> dict[str, Any]:
    try:
        formula = structure.composition.reduced_formula
    except Exception:
        formula = None
    lattice = getattr(structure, "lattice", None)
    return {
        "formula": formula,
        "space_group": None,
        "lattice": {
            "a": getattr(lattice, "a", None),
            "b": getattr(lattice, "b", None),
            "c": getattr(lattice, "c", None),
            "alpha": getattr(lattice, "alpha", None),
            "beta": getattr(lattice, "beta", None),
            "gamma": getattr(lattice, "gamma", None),
        },
        "site_count": len(structure),
        "species": sorted({str(site.specie) for site in structure}),
        "coordination_environments": [],
    }


def run(request: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload(request)
    fixture = request.get("structure_fixture")
    if isinstance(fixture, dict):
        payload["status"] = "success"
        payload["provider_health"] = _provider_health("fixture")
        payload["primary_result"] = _summarize_fixture(fixture)
        payload["tool_trace"].append({"step": "summarize_structure_fixture", "status": "success"})
        return payload

    structure_path = request.get("structure_path")
    if not isinstance(structure_path, str) or not structure_path.strip():
        payload["errors"].append(
            {"code": "missing_structure_path", "message": "Provide `structure_path` or `structure_fixture`."}
        )
        return payload

    try:
        import pymatgen  # type: ignore
        from pymatgen.core import Structure  # type: ignore
    except ImportError:
        payload["provider_health"] = _provider_health(
            "missing_dependency",
            message="pymatgen is not installed. Install `chemqa[chem-materials]`.",
        )
        payload["errors"].append({"code": "missing_dependency", "message": "pymatgen is not installed."})
        return payload

    structure = Structure.from_file(structure_path)
    payload["status"] = "success"
    payload["provider_health"] = _provider_health("available", version=str(getattr(pymatgen, "__version__", "")))
    payload["primary_result"] = _summarize_pymatgen_structure(structure)
    payload["source_trace"].append({"type": "local_structure_file", "path": structure_path})
    payload["tool_trace"].append({"step": "Structure.from_file", "status": "success", "path": structure_path})
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    start = time.time()
    request_path = Path(args.request_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except Exception as exc:
        request = {}
        payload = _empty_payload(request)
        payload["errors"].append({"code": "invalid_request_json", "message": str(exc)})
    else:
        if not isinstance(request, dict):
            payload = _empty_payload(request)
            payload["errors"].append({"code": "invalid_request_type", "message": "Request JSON must be an object."})
        else:
            payload = run(request)

    payload.setdefault("tool_trace", []).append(
        {"step": "structure_summary", "status": payload.get("status", "error"), "elapsed_ms": int((time.time() - start) * 1000)}
    )
    result_path = _write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    _write_payload(output_dir, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
