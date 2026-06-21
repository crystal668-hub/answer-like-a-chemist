from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


SKILL_NAME = "xtb-cli"
RESULT_FILE_NAME = "result.json"
RUN_TYPES = {"single_point", "opt", "hess", "ohess", "vomega", "vfukui"}
SOLVENT_MODELS = {"alpb", "gbsa"}
FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"


class SkillError(Exception):
    def __init__(self, code: str, message: str, *, primary_result: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.primary_result = primary_result or {}


class RequestError(SkillError):
    pass


class ProviderError(SkillError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local xTB calculations through a structured JSON skill contract.")
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def empty_payload(request: Any) -> dict[str, Any]:
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


def json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def write_payload(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / RESULT_FILE_NAME
    result_path.write_text(json_dumps(payload), encoding="utf-8")
    return result_path


def append_error(payload: dict[str, Any], code: str, message: str) -> None:
    payload.setdefault("errors", []).append({"code": code, "message": message})


def append_warning(payload: dict[str, Any], code: str, message: str) -> None:
    payload.setdefault("warnings", []).append({"code": code, "message": message})


def load_request_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RequestError("missing_request_json", f"Request JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RequestError("invalid_request_json", f"Request JSON is not valid JSON: {exc}") from exc


def ensure_mapping_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise RequestError("invalid_request_type", "Top-level request JSON must be an object.")
    return request


def resolve_xtb() -> str:
    executable = shutil.which("xtb")
    if not executable:
        raise ProviderError("missing_executable", "xtb executable not found in PATH.")
    return executable


def xtb_version(executable: str) -> tuple[str, str]:
    completed = subprocess.run(
        [executable, "--version"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    match = re.search(r"xtb version\s+([^\s]+(?:\s+\([^)]+\))?)", output, re.IGNORECASE)
    version = match.group(1).strip() if match else ""
    return version, output


def provider_health(status: str, *, version: str = "", message: str = "", executable: str = "") -> dict[str, Any]:
    health: dict[str, Any] = {
        SKILL_NAME: {
            "available": status == "available",
            "status": status,
        }
    }
    if version:
        health[SKILL_NAME]["version"] = version
    if message:
        health[SKILL_NAME]["message"] = message
    if executable:
        health[SKILL_NAME]["executable"] = executable
    return health


def get_int(request: dict[str, Any], key: str, default: int) -> int:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError("invalid_field", f"Request field `{key}` must be an integer.")
    return value


def get_timeout(request: dict[str, Any]) -> int:
    value = request.get("timeout_seconds", 60)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RequestError("invalid_field", "Request field `timeout_seconds` must be a positive number.")
    return int(value)


def validate_geometry_text(text: str) -> None:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise RequestError("invalid_geometry", "XYZ geometry must include atom count, comment line, and atom coordinates.")
    try:
        atom_count = int(lines[0].strip())
    except ValueError as exc:
        raise RequestError("invalid_geometry", "First XYZ line must be an integer atom count.") from exc
    if atom_count <= 0:
        raise RequestError("invalid_geometry", "XYZ atom count must be positive.")
    if len(lines) < atom_count + 2:
        raise RequestError("invalid_geometry", "XYZ geometry has fewer coordinate rows than the atom count.")
    for index, line in enumerate(lines[2 : atom_count + 2], start=1):
        parts = line.split()
        if len(parts) < 4:
            raise RequestError("invalid_geometry", f"XYZ atom row {index} must include symbol and three coordinates.")
        for raw in parts[1:4]:
            try:
                value = float(raw)
            except ValueError as exc:
                raise RequestError("invalid_geometry", f"XYZ atom row {index} has a non-numeric coordinate.") from exc
            if not math.isfinite(value):
                raise RequestError("invalid_geometry", f"XYZ atom row {index} has a non-finite coordinate.")


def resolve_geometry(request: dict[str, Any], execution_dir: Path) -> tuple[str, dict[str, Any]]:
    geometry_xyz = request.get("geometry_xyz")
    geometry_path = request.get("geometry_path")
    if isinstance(geometry_xyz, str) and geometry_xyz.strip():
        validate_geometry_text(geometry_xyz)
        target = execution_dir / "candidate.xyz"
        target.write_text(geometry_xyz.rstrip() + "\n", encoding="utf-8")
        return str(target), {"type": "inline_xyz", "path": str(target), "atom_count": int(geometry_xyz.splitlines()[0].strip())}
    if isinstance(geometry_path, str) and geometry_path.strip():
        source = Path(geometry_path).expanduser().resolve()
        if not source.is_file():
            raise RequestError("missing_geometry_file", f"Geometry file does not exist: {geometry_path}")
        text = source.read_text(encoding="utf-8")
        validate_geometry_text(text)
        target = execution_dir / "candidate.xyz"
        target.write_text(text.rstrip() + "\n", encoding="utf-8")
        return str(target), {
            "type": "local_file",
            "source_path": str(source),
            "path": str(target),
            "atom_count": int(text.splitlines()[0].strip()),
        }
    raise RequestError("missing_geometry", "Provide `geometry_xyz` or `geometry_path`.")


def build_xtb_command(request: dict[str, Any], *, executable: str, geometry_path: str, execution_dir: Path) -> list[str]:
    run_type = str(request.get("run_type") or "single_point").strip().lower()
    if run_type not in RUN_TYPES:
        raise RequestError("unsupported_run_type", f"Unsupported run_type `{run_type}`. Expected one of: {', '.join(sorted(RUN_TYPES))}.")
    gfn = get_int(request, "gfn", 2)
    if gfn not in {0, 1, 2}:
        raise RequestError("invalid_field", "Request field `gfn` must be 0, 1, or 2.")
    charge = get_int(request, "charge", 0)
    uhf = get_int(request, "uhf", 0)
    if uhf < 0:
        raise RequestError("invalid_field", "Request field `uhf` must be non-negative.")

    command = [executable, geometry_path, "--gfn", str(gfn), "--chrg", str(charge), "--uhf", str(uhf)]
    if run_type != "single_point":
        command.append(f"--{run_type}")

    solvent_model = str(request.get("solvent_model") or "").strip().lower()
    solvent = str(request.get("solvent") or "").strip()
    if solvent_model or solvent:
        if solvent_model not in SOLVENT_MODELS:
            raise RequestError("invalid_field", "Request field `solvent_model` must be `alpb` or `gbsa` when solvent is provided.")
        if not solvent:
            raise RequestError("invalid_field", "Request field `solvent` is required when `solvent_model` is set.")
        command.extend([f"--{solvent_model}", solvent])

    if request.get("write_json_control") is True:
        control_path = execution_dir / "xcontrol.inp"
        control_path.write_text("$write\n   json=true\n$end\n", encoding="utf-8")
        command.extend(["--input", str(control_path)])
    elif request.get("write_json_control") not in {None, False}:
        raise RequestError("invalid_field", "Request field `write_json_control` must be a boolean.")
    return command


def run_xtb(command: list[str], *, cwd: Path, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def parse_last_float(pattern: str, text: str) -> float | None:
    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE | re.MULTILINE))
    if not matches:
        return None
    raw = matches[-1].group(1)
    try:
        return float(raw)
    except ValueError:
        return None


def parse_orbital_energy(marker: str, text: str) -> float | None:
    values: list[float] = []
    marker_re = re.compile(rf"^(?P<line>.*\({re.escape(marker)}\).*)$", re.IGNORECASE | re.MULTILINE)
    for match in marker_re.finditer(text):
        floats = re.findall(FLOAT_RE, match.group("line"))
        if len(floats) >= 2:
            try:
                values.append(float(floats[-2]))
            except ValueError:
                continue
    return values[-1] if values else None


def parse_xtb_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"
    parsed: dict[str, Any] = {
        "normal_termination": bool(re.search(r"normal termination of xtb", combined, re.IGNORECASE)),
    }
    field_patterns = {
        "total_energy_Eh": rf"TOTAL\s+ENERGY\s+({FLOAT_RE})\s+Eh",
        "homo_lumo_gap_eV": rf"HOMO[-\s]?LUMO\s+GAP\s+({FLOAT_RE})\s+eV",
        "gsolv_Eh": rf"\bGsolv\s+({FLOAT_RE})\s+Eh",
        "imaginary_frequency_count": rf"(?:number of )?imaginary frequencies:?\s+({FLOAT_RE})",
    }
    for field, pattern in field_patterns.items():
        value = parse_last_float(pattern, combined)
        if value is not None:
            parsed[field] = int(value) if field == "imaginary_frequency_count" else value

    homo = parse_orbital_energy("HOMO", combined)
    lumo = parse_orbital_energy("LUMO", combined)
    if homo is not None:
        parsed["homo_energy_Eh"] = homo
    if lumo is not None:
        parsed["lumo_energy_Eh"] = lumo

    dipole = parse_last_float(rf"total\s+({FLOAT_RE})\s+Debye", combined)
    if dipole is not None:
        parsed["dipole_Debye"] = dipole

    polarizability = parse_last_float(rf"polarizability[^\n\r]*?\s+({FLOAT_RE})(?:\s|$)", combined)
    if polarizability is not None:
        parsed["polarizability_au"] = polarizability

    electrophilicity = parse_last_float(rf"Global electrophilicity index \(eV\)\s*[:=]?\s*({FLOAT_RE})", combined)
    if electrophilicity is not None:
        parsed["global_electrophilicity_eV"] = electrophilicity
    return parsed


def collect_artifacts(execution_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {"working_directory": str(execution_dir)}
    for name in ("candidate.xyz", "xtbopt.xyz", "xtbopt.coord", "xtbhess.coord", "xtbout.json"):
        path = execution_dir / name
        if path.is_file():
            artifacts[name] = str(path)
    return artifacts


def run(request: dict[str, Any], *, output_dir: Path) -> dict[str, Any]:
    payload = empty_payload(request)
    executable = resolve_xtb()
    version, version_output = xtb_version(executable)
    payload["provider_health"] = provider_health("available", version=version, executable=executable)
    payload["tool_trace"].append({"step": "xtb_version", "status": "success", "version": version, "stdout_excerpt": version_output[:500]})

    timeout_seconds = get_timeout(request)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_dir = output_dir / "xtb-work"
    if execution_dir.exists():
        shutil.rmtree(execution_dir)
    execution_dir.mkdir(parents=True)
    geometry_path, geometry_source = resolve_geometry(request, execution_dir)
    payload["source_trace"].append(geometry_source)
    command = build_xtb_command(request, executable=executable, geometry_path=geometry_path, execution_dir=execution_dir)
    start = time.time()
    try:
        completed = run_xtb(command, cwd=execution_dir, timeout_seconds=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise ProviderError("provider_timeout", f"xtb timed out after {timeout_seconds} seconds.")
    elapsed_ms = int((time.time() - start) * 1000)

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    parsed = parse_xtb_output(stdout, stderr)
    artifacts = collect_artifacts(execution_dir)
    run_type = str(request.get("run_type") or "single_point").strip().lower()
    primary_result: dict[str, Any] = {
        "run_type": run_type,
        "method": f"GFN{request.get('gfn', 2)}-xTB",
        "gfn": get_int(request, "gfn", 2),
        "charge": get_int(request, "charge", 0),
        "uhf": get_int(request, "uhf", 0),
        "command": command,
        "returncode": completed.returncode,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "stdout_excerpt": stdout[:4000],
        "stderr_excerpt": stderr[:4000],
        "artifacts": artifacts,
        **parsed,
    }
    optimized_geometry = execution_dir / "xtbopt.xyz"
    if optimized_geometry.is_file():
        primary_result["optimized_geometry_xyz"] = optimized_geometry.read_text(encoding="utf-8")
    json_output = execution_dir / "xtbout.json"
    if json_output.is_file():
        try:
            primary_result["xtb_json"] = json.loads(json_output.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload["warnings"].append({"code": "invalid_xtb_json", "message": "xtbout.json was present but not valid JSON."})

    payload["primary_result"] = primary_result
    payload["tool_trace"].append({"step": "xtb_run", "status": "success" if completed.returncode == 0 else "error", "elapsed_ms": elapsed_ms})
    if completed.returncode == 0:
        payload["status"] = "success"
    else:
        payload["status"] = "error"
        append_error(payload, "xtb_failed", f"xtb exited with return code {completed.returncode}.")
    if not parsed.get("normal_termination"):
        append_warning(payload, "normal_termination_not_detected", "xTB output did not include a normal termination marker.")
    return payload


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    request_path = Path(args.request_json).expanduser().resolve()
    request: Any = {}
    payload = empty_payload(request)
    start = time.time()
    try:
        request = load_request_json(request_path)
        payload = empty_payload(request)
        payload["tool_trace"].append({"step": "load_request_json", "status": "success", "path": str(request_path)})
        request_mapping = ensure_mapping_request(request)
        payload = run(request_mapping, output_dir=output_dir)
    except RequestError as exc:
        payload = empty_payload(request)
        append_error(payload, exc.code, exc.message)
        payload["primary_result"] = dict(exc.primary_result)
        payload["status"] = "error"
    except ProviderError as exc:
        payload = empty_payload(request)
        payload["provider_health"] = provider_health(exc.code)
        append_error(payload, exc.code, exc.message)
        payload["primary_result"] = dict(exc.primary_result)
        payload["status"] = "error"
    except Exception as exc:  # pragma: no cover - defensive contract guard
        payload = empty_payload(request)
        append_error(payload, "unexpected_error", str(exc))
        payload["status"] = "error"

    elapsed_ms = int((time.time() - start) * 1000)
    payload.setdefault("tool_trace", []).append({"step": "xtb_runner", "status": payload.get("status", "error"), "elapsed_ms": elapsed_ms})
    result_path = write_payload(output_dir, payload)
    payload.setdefault("tool_trace", []).append({"step": "write_result", "status": "success", "path": str(result_path)})
    write_payload(output_dir, payload)
    if args.json:
        print(json_dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
