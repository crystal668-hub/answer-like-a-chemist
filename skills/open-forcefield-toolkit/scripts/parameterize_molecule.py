from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "open-forcefield-toolkit"


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


def _provider_health(status: str, *, message: str | None = None) -> dict[str, Any]:
    health = {"status": status, "available": status == "available"}
    if message:
        health["message"] = message
    return {SKILL_NAME: health}


def _write_payload(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return result_path


def _plan_from_request(request: dict[str, Any]) -> dict[str, Any]:
    smiles = request.get("smiles")
    if not isinstance(smiles, str) or not smiles.strip():
        raise ValueError("`smiles` is required for parameterization planning.")
    return {
        "smiles": smiles.strip(),
        "force_field": request.get("force_field") or "openff-2.0.0.offxml",
        "partial_charge_method": request.get("partial_charge_method") or "am1bcc",
        "output_formats": request.get("output_formats") or [],
        "execute": bool(request.get("execute")),
    }


def run(request: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload(request)
    try:
        plan = _plan_from_request(request)
    except ValueError as exc:
        payload["errors"].append({"code": "invalid_request", "message": str(exc)})
        return payload

    if not plan["execute"]:
        payload["status"] = "success"
        payload["provider_health"] = _provider_health("not_required")
        payload["primary_result"] = {
            "mode": "dry_run",
            "parameterization_plan": plan,
            "requires_openff_toolkit": True,
        }
        payload["tool_trace"].append({"step": "build_parameterization_plan", "status": "success"})
        return payload

    try:
        from openff.toolkit.topology import Molecule  # type: ignore
        from openff.toolkit.typing.engines.smirnoff import ForceField  # type: ignore
    except ImportError:
        payload["provider_health"] = _provider_health(
            "missing_dependency",
            message="OpenFF Toolkit is not installed. Use a conda/preinstalled OpenFF environment for execution.",
        )
        payload["errors"].append({"code": "missing_dependency", "message": "OpenFF Toolkit is not installed."})
        return payload

    molecule = Molecule.from_smiles(plan["smiles"])
    molecule.generate_conformers(n_conformers=int(request.get("n_conformers") or 1))
    if plan["partial_charge_method"]:
        molecule.assign_partial_charges(partial_charge_method=plan["partial_charge_method"])
    force_field = ForceField(plan["force_field"])
    interchange = force_field.create_interchange(molecule.to_topology())
    payload["status"] = "success"
    payload["provider_health"] = _provider_health("available")
    payload["primary_result"] = {
        "mode": "executed",
        "smiles": plan["smiles"],
        "force_field": plan["force_field"],
        "partial_charge_method": plan["partial_charge_method"],
        "atom_count": molecule.n_atoms,
        "has_interchange": interchange is not None,
    }
    payload["tool_trace"].append({"step": "openff_parameterization", "status": "success"})
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
        {"step": "parameterize_molecule", "status": payload.get("status", "error"), "elapsed_ms": int((time.time() - start) * 1000)}
    )
    result_path = _write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    _write_payload(output_dir, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
