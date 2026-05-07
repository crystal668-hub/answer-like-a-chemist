from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "chembl-database"


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


def _summarize_activities(records: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    sorted_records = sorted(records, key=lambda item: _numeric_value(item.get("standard_value")))
    target_ids = sorted({str(item.get("target_chembl_id")) for item in records if item.get("target_chembl_id")})
    molecule_ids = sorted({str(item.get("molecule_chembl_id")) for item in records if item.get("molecule_chembl_id")})
    return {
        "query": {
            "target": request.get("target"),
            "standard_type": request.get("standard_type"),
            "max_standard_value": request.get("max_standard_value"),
            "standard_units": request.get("standard_units"),
        },
        "activity_count": len(records),
        "target_chembl_ids": target_ids,
        "molecule_chembl_ids": molecule_ids,
        "best_activity": sorted_records[0] if sorted_records else None,
    }


def _numeric_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def _records_from_fixture(request: dict[str, Any]) -> list[dict[str, Any]] | None:
    records = request.get("activity_fixture")
    if records is None:
        return None
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError("`activity_fixture` must be a list of objects.")
    return records


def run(request: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_payload(request)
    try:
        fixture_records = _records_from_fixture(request)
    except ValueError as exc:
        payload["errors"].append({"code": "invalid_fixture", "message": str(exc)})
        return payload

    if fixture_records is not None:
        payload["status"] = "success"
        payload["provider_health"] = _provider_health("fixture")
        payload["primary_result"] = _summarize_activities(fixture_records, request)
        payload["candidates"] = fixture_records
        payload["tool_trace"].append({"step": "summarize_activity_fixture", "status": "success"})
        return payload

    try:
        from chembl_webresource_client.new_client import new_client  # type: ignore
    except ImportError:
        payload["provider_health"] = _provider_health(
            "missing_dependency",
            message="chembl_webresource_client is not installed. Install `chemqa[chem-bioactivity]`.",
        )
        payload["errors"].append({"code": "missing_dependency", "message": "chembl_webresource_client is not installed."})
        return payload

    target = request.get("target")
    target_chembl_id = request.get("target_chembl_id")
    if not target_chembl_id and isinstance(target, str) and target.strip():
        targets = list(new_client.target.filter(pref_name__icontains=target.strip())[:5])
        payload["source_trace"].append({"type": "chembl_target_search", "target": target, "count": len(targets)})
        if targets:
            target_chembl_id = targets[0].get("target_chembl_id")

    if not target_chembl_id:
        payload["errors"].append({"code": "missing_target", "message": "Provide `target_chembl_id` or searchable `target`."})
        return payload

    standard_type = str(request.get("standard_type") or "IC50")
    standard_units = str(request.get("standard_units") or "nM")
    filters: dict[str, Any] = {
        "target_chembl_id": target_chembl_id,
        "standard_type": standard_type,
        "standard_units": standard_units,
    }
    if request.get("max_standard_value") is not None:
        filters["standard_value__lte"] = request["max_standard_value"]

    limit = int(request.get("limit") or 20)
    records = list(new_client.activity.filter(**filters)[:limit])
    payload["status"] = "success"
    payload["provider_health"] = _provider_health("available")
    payload["primary_result"] = _summarize_activities(records, request | {"target_chembl_id": target_chembl_id})
    payload["candidates"] = records
    payload["source_trace"].append({"type": "chembl_activity_query", "filters": filters, "limit": limit})
    payload["tool_trace"].append({"step": "chembl_activity_filter", "status": "success"})
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
        {"step": "bioactivity_query", "status": payload.get("status", "error"), "elapsed_ms": int((time.time() - start) * 1000)}
    )
    result_path = _write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    _write_payload(output_dir, payload)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
