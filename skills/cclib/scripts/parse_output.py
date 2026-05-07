from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "cclib"


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


def _summarize_parsed_data(data: Any, *, source: str) -> dict[str, Any]:
    attributes = _data_to_mapping(data)
    summary = {
        "source": source,
        "package": attributes.get("package"),
        "natom": attributes.get("natom"),
        "final_scf_energy_ev": _last_value(attributes.get("scfenergies")),
        "homo_indices": attributes.get("homos"),
        "mo_energy_sets": _length(attributes.get("moenergies")),
        "vibrational_frequency_count": _length(attributes.get("vibfreqs")),
        "imaginary_frequencies": [
            value for value in attributes.get("vibfreqs", []) if isinstance(value, (int, float)) and value < 0
        ],
        "metadata": attributes.get("metadata") or {},
    }
    return summary


def _data_to_mapping(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        return dict(data)
    mapping: dict[str, Any] = {}
    for name in (
        "package",
        "natom",
        "scfenergies",
        "homos",
        "moenergies",
        "vibfreqs",
        "metadata",
    ):
        if hasattr(data, name):
            value = getattr(data, name)
            if hasattr(value, "tolist"):
                value = value.tolist()
            mapping[name] = value
    return mapping


def _last_value(value: Any) -> Any:
    if isinstance(value, list) and value:
        return value[-1]
    return None


def _length(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def run(request: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload(request)
    fixture = request.get("parsed_fixture")
    if isinstance(fixture, dict):
        payload["status"] = "success"
        payload["provider_health"] = _provider_health("fixture")
        payload["primary_result"] = _summarize_parsed_data(fixture, source="parsed_fixture")
        payload["tool_trace"].append({"step": "summarize_parsed_fixture", "status": "success"})
        return payload

    output_path = request.get("output_path")
    if not isinstance(output_path, str) or not output_path.strip():
        payload["errors"].append(
            {"code": "missing_output_path", "message": "Provide `output_path` or `parsed_fixture`."}
        )
        return payload

    try:
        import cclib  # type: ignore
    except ImportError:
        payload["provider_health"] = _provider_health(
            "missing_dependency",
            message="cclib is not installed. Install `chemqa[chem-quantum-parse]` to parse output files.",
        )
        payload["errors"].append({"code": "missing_dependency", "message": "cclib is not installed."})
        return payload

    parsed = cclib.parser.parse(output_path)
    if parsed is None:
        payload["errors"].append({"code": "parse_failed", "message": f"cclib could not parse {output_path}."})
        return payload

    payload["status"] = "success"
    payload["provider_health"] = _provider_health("available", version=str(getattr(cclib, "__version__", "")))
    payload["primary_result"] = _summarize_parsed_data(parsed, source=output_path)
    payload["source_trace"].append({"type": "local_file", "path": output_path})
    payload["tool_trace"].append({"step": "cclib.parser.parse", "status": "success", "path": output_path})
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
        {"step": "parse_output", "status": payload.get("status", "error"), "elapsed_ms": int((time.time() - start) * 1000)}
    )
    result_path = _write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    _write_payload(output_dir, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
