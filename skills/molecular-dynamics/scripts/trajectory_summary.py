from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "molecular-dynamics"


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


def _rmsd(frame: list[list[float]], reference: list[list[float]]) -> float:
    total = 0.0
    count = min(len(frame), len(reference))
    if count == 0:
        return 0.0
    for point, ref in zip(frame[:count], reference[:count]):
        total += sum((float(a) - float(b)) ** 2 for a, b in zip(point[:3], ref[:3]))
    return math.sqrt(total / count)


def _summarize_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    frames = fixture.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError("trajectory fixture requires non-empty `frames`.")
    reference = frames[0]
    rmsd_values = [_rmsd(frame, reference) for frame in frames if isinstance(frame, list)]
    return {
        "frame_count": len(frames),
        "atom_count": len(reference) if isinstance(reference, list) else 0,
        "rmsd": {
            "min": min(rmsd_values),
            "max": max(rmsd_values),
            "final": rmsd_values[-1],
        },
        "rmsf_requested": bool(fixture.get("rmsf")),
        "contact_map_requested": bool(fixture.get("contact_map")),
    }


def run(request: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload(request)
    fixture = request.get("trajectory_fixture")
    if isinstance(fixture, dict):
        try:
            summary = _summarize_fixture(fixture)
        except ValueError as exc:
            payload["errors"].append({"code": "invalid_fixture", "message": str(exc)})
            return payload
        payload["status"] = "success"
        payload["provider_health"] = _provider_health("fixture")
        payload["primary_result"] = summary
        payload["tool_trace"].append({"step": "summarize_trajectory_fixture", "status": "success"})
        return payload

    topology_path = request.get("topology_path")
    trajectory_path = request.get("trajectory_path")
    if not isinstance(topology_path, str) or not isinstance(trajectory_path, str):
        payload["errors"].append(
            {"code": "missing_trajectory_inputs", "message": "Provide topology_path and trajectory_path, or trajectory_fixture."}
        )
        return payload

    try:
        import MDAnalysis as mda  # type: ignore
    except ImportError:
        payload["provider_health"] = _provider_health(
            "missing_dependency",
            message="MDAnalysis is not installed. Install `chemqa[chem-md]`.",
        )
        payload["errors"].append({"code": "missing_dependency", "message": "MDAnalysis is not installed."})
        return payload

    universe = mda.Universe(topology_path, trajectory_path)
    payload["status"] = "success"
    payload["provider_health"] = _provider_health("available", version=str(getattr(mda, "__version__", "")))
    payload["primary_result"] = {
        "frame_count": len(universe.trajectory),
        "atom_count": len(universe.atoms),
        "selection": request.get("selection") or "all",
    }
    payload["source_trace"].append({"type": "trajectory", "topology_path": topology_path, "trajectory_path": trajectory_path})
    payload["tool_trace"].append({"step": "MDAnalysis.Universe", "status": "success"})
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
        {"step": "trajectory_summary", "status": payload.get("status", "error"), "elapsed_ms": int((time.time() - start) * 1000)}
    )
    result_path = _write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    _write_payload(output_dir, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
