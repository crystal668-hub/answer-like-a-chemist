from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarking.runtime.attempt_observability import (
    DEPENDENCY_MANIFEST_SCHEMA_VERSION,
    package_delta,
)
from benchmarking.runtime.transcript_index import TranscriptIndex
from benchmarking.runtime.transcript_tools import (
    background_session_id,
    is_terminal_process_event,
    operation_outcome,
    tool_events_from_transcript,
)

RunSubprocess = Callable[..., subprocess.CompletedProcess[str]]
FORBIDDEN_DISTRIBUTIONS = frozenset({"verifier-grounded-benchmark"})


@dataclass(frozen=True)
class AttemptPythonEnvironment:
    venv_dir: Path
    python: Path
    pip: Path
    bin_dir: Path
    cache_dir: Path
    tool_bin_dir: Path
    native_tools: dict[str, str]
    bootstrap_python: str
    pypi_cutoff: str

    def to_env(self) -> dict[str, str]:
        return {
            "BENCHMARK_ATTEMPT_VENV": str(self.venv_dir),
            "BENCHMARK_ATTEMPT_PYTHON": str(self.python),
            "BENCHMARK_ATTEMPT_VENV_BIN": str(self.bin_dir),
            "BENCHMARK_ATTEMPT_UV_CACHE": str(self.cache_dir),
            "BENCHMARK_PYPI_CUTOFF": self.pypi_cutoff,
            "VIRTUAL_ENV": str(self.venv_dir),
            "UV_PYTHON": str(self.python),
            "UV_CACHE_DIR": str(self.cache_dir),
            "UV_DEFAULT_INDEX": "https://pypi.org/simple",
            "UV_EXCLUDE_NEWER": self.pypi_cutoff,
            "PATH": os.pathsep.join(
                [str(self.bin_dir), str(self.tool_bin_dir), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
            ),
        }


def create_attempt_environment(
    root: Path,
    *,
    bootstrap_python: str | None = None,
    pypi_cutoff: str | None = None,
    uv_executable: str | None = None,
    run_subprocess: RunSubprocess = subprocess.run,
    timeout_seconds: int = 120,
    cache_dir: Path | None = None,
) -> AttemptPythonEnvironment:
    root = Path(root).expanduser().resolve()
    venv_dir = root / "venv"
    cache_dir = cache_dir or root / ".uv-cache"
    tool_bin_dir = root / ".runtime-bin"
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    tool_bin_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = str(bootstrap_python or Path(sys.executable).expanduser().resolve())
    cutoff = pypi_cutoff or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    uv = uv_executable or shutil.which("uv")
    if not uv:
        raise FileNotFoundError("uv executable not found in PATH")
    native_tools = _materialize_native_tools(
        tool_bin_dir,
        uv=uv,
        environment={
            "VIRTUAL_ENV": str(venv_dir),
            "UV_PYTHON": str(venv_dir / "bin" / "python"),
            "UV_DEFAULT_INDEX": "https://pypi.org/simple",
            "UV_EXCLUDE_NEWER": cutoff,
            "UV_CACHE_DIR": str(cache_dir),
        },
    )
    env = os.environ.copy()
    env.update(
        {
            "UV_CACHE_DIR": str(cache_dir),
            "UV_DEFAULT_INDEX": "https://pypi.org/simple",
            "UV_EXCLUDE_NEWER": cutoff,
            "UV_NO_PROJECT": "1",
        }
    )
    command = [
        uv,
        "venv",
        "--seed",
        "--no-project",
        "--no-python-downloads",
        "--python",
        bootstrap,
        str(venv_dir),
    ]
    completed = run_subprocess(
        command,
        cwd=str(root),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"attempt venv creation failed: {detail[:1000]}")
    python = venv_dir / "bin" / "python"
    pip = venv_dir / "bin" / "pip"
    if not python.is_file() or not pip.is_file():
        raise RuntimeError(f"attempt venv is missing seeded Python/pip: {venv_dir}")
    probe = run_subprocess(
        [str(python), "-c", "import sys; assert sys.prefix != sys.base_prefix; import pip"],
        cwd=str(root),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "").strip()
        raise RuntimeError(f"attempt venv validation failed: {detail[:1000]}")
    shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return AttemptPythonEnvironment(
        venv_dir=venv_dir,
        python=python,
        pip=pip,
        bin_dir=venv_dir / "bin",
        cache_dir=cache_dir,
        tool_bin_dir=tool_bin_dir,
        native_tools=native_tools,
        bootstrap_python=bootstrap,
        pypi_cutoff=cutoff,
    )


def collect_dependency_manifest(
    environment: AttemptPythonEnvironment,
    *,
    identity: dict[str, Any] | None = None,
    native_tools: dict[str, str] | None = None,
    base_env: dict[str, str] | None = None,
    run_subprocess: RunSubprocess = subprocess.run,
    install_events: list[dict[str, Any]] | None = None,
    baseline_distributions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    env = dict(base_env or os.environ)
    env.update(environment.to_env())

    def collect(command, **kwargs):
        try:
            return run_subprocess(command, **kwargs)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(command, 1, "", f"{type(exc).__name__}: {exc}")

    freeze = collect(
        ["uv", "pip", "freeze", "--python", str(environment.python)],
        cwd=str(environment.venv_dir.parent),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    packages = [line.strip() for line in (freeze.stdout or "").splitlines() if line.strip()]
    try:
        replay = _build_replay_lock(environment, packages, env=env, run_subprocess=run_subprocess)
    except (OSError, subprocess.TimeoutExpired) as exc:
        replay = {"status": "unavailable", "error": str(exc)}
    inventory = collect_distribution_inventory(
        environment,
        base_env=env,
        run_subprocess=run_subprocess,
    )
    distributions = inventory.get("distributions")
    python_info = inventory.get("python") if isinstance(inventory.get("python"), dict) else {}
    return {
        "schema_version": DEPENDENCY_MANIFEST_SCHEMA_VERSION,
        "identity": dict(identity or {}),
        "python": {
            "bootstrap": environment.bootstrap_python,
            "executable": str(environment.python),
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            **python_info,
        },
        "venv": {
            "path": str(environment.venv_dir),
            "system_site_packages": False,
            "seeded_pip": environment.pip.is_file(),
            "cache_dir": str(environment.cache_dir),
        },
        "pypi": {
            "default_index": "https://pypi.org/simple",
            "cutoff": environment.pypi_cutoff,
            "freeze_returncode": freeze.returncode,
            "freeze": packages,
            "replay_lock": replay,
        },
        "baseline_distributions": baseline_distributions,
        "distributions": distributions,
        "effective_distributions": distributions,
        "distribution_delta": package_delta(
            baseline_distributions,
            distributions,
            distributions,
        ),
        "collection": {
            "freeze": {"returncode": freeze.returncode, "stderr": freeze.stderr},
            "inventory": inventory.get("collection") or {},
        },
        "install_events": list(install_events or []),
        "native_tools": _native_tool_fingerprints(native_tools or environment.native_tools),
        "credentials": {
            "environment_names": sorted(
                key for key in env if key.endswith("_API_KEY") or key.endswith("_TOKEN")
            )
        },
    }


def collect_distribution_inventory(
    environment: AttemptPythonEnvironment,
    *,
    base_env: dict[str, str] | None = None,
    run_subprocess: RunSubprocess = subprocess.run,
) -> dict[str, Any]:
    env = dict(base_env or os.environ)
    env.update(environment.to_env())
    try:
        probe = run_subprocess(
            [
                str(environment.python),
                "-c",
                "import hashlib, importlib.metadata as m, json, sys, platform; "
                "rows=[]; "
                "[(rows.append({'name':d.metadata['Name'],'version':d.version,'direct_url':d.read_text('direct_url.json') or '',"
                "'record_present':d.read_text('RECORD') is not None, "
                "'record_sha256':hashlib.sha256((d.read_text('RECORD') or '').encode()).hexdigest()})) "
                "for d in m.distributions() if d.metadata.get('Name')]; "
                "print(json.dumps({'distributions':sorted(rows,key=lambda x:x['name'].lower()),"
                "'python':{'executable':sys.executable,'prefix':sys.prefix,'base_prefix':sys.base_prefix,"
                "'version':platform.python_version(),'implementation':platform.python_implementation(),"
                "'platform':platform.platform(),'machine':platform.machine()}}))",
            ],
            cwd=str(environment.venv_dir.parent),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "distributions": None,
            "python": {},
            "collection": {"returncode": 1, "stderr": f"{type(exc).__name__}: {exc}"},
        }
    distributions = None
    python_info = {}
    if probe.returncode == 0:
        try:
            payload = json.loads(probe.stdout)
            distributions = payload["distributions"]
            python_info = payload["python"]
        except (json.JSONDecodeError, TypeError, ValueError, KeyError):
            distributions = None
    return {
        "distributions": distributions,
        "python": python_info,
        "collection": {"returncode": probe.returncode, "stderr": probe.stderr},
    }


def _build_replay_lock(
    environment: AttemptPythonEnvironment,
    packages: list[str],
    *,
    env: dict[str, str],
    run_subprocess: RunSubprocess,
) -> dict[str, Any]:
    notes_dir = environment.venv_dir.parent / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    source = notes_dir / "replay-requirements.in"
    lock = notes_dir / "replay-requirements.txt"
    source.write_text("\n".join(packages) + ("\n" if packages else ""), encoding="utf-8")
    command = [
        "uv",
        "pip",
        "compile",
        "--generate-hashes",
        "--no-sources",
        "--python",
        str(environment.python),
        "--exclude-newer",
        environment.pypi_cutoff,
        "--output-file",
        str(lock),
        str(source),
    ]
    completed = run_subprocess(
        command,
        cwd=str(environment.venv_dir.parent),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    content = lock.read_text(encoding="utf-8") if completed.returncode == 0 and lock.is_file() else ""
    return {
        "status": "generated" if content else "unavailable",
        "returncode": completed.returncode,
        "path": "scratch/notes/replay-requirements.txt",
        "sha256": hashlib.sha256(content.encode()).hexdigest() if content else "",
    }


def dependency_install_events(
    transcript_path: str | Path | None,
    *,
    transcript_index: TranscriptIndex | None = None,
) -> list[dict[str, Any]]:
    if not transcript_path:
        return []
    path = Path(transcript_path).expanduser()
    if path.is_symlink() or not path.is_file():
        return []
    if transcript_index is not None and transcript_index.path != path:
        raise ValueError("transcript index path does not match dependency transcript")
    index = transcript_index or TranscriptIndex.from_path(path)
    payloads = [(entry.line_number, entry.payload) for entry in index.entries]
    events, _ = tool_events_from_transcript(payloads)
    process_results: dict[str, list[Any]] = {}
    for event in events:
        if event.tool_name.strip().lower() != "process" or not isinstance(event.arguments, dict):
            continue
        session_id = str(event.arguments.get("sessionId") or event.arguments.get("session_id") or "").strip()
        if session_id and event.result is not None:
            process_results.setdefault(session_id, []).append(event)

    captured: list[dict[str, Any]] = []
    for event in events:
        arguments = event.arguments if isinstance(event.arguments, dict) else {}
        command = str(arguments.get("command") or "").strip()
        if not _is_dependency_command(command):
            continue
        result = event.result
        result_line = event.result_line
        outcome = operation_outcome(result)
        session_id = background_session_id(event)
        if session_id:
            followups = process_results.get(session_id, [])
            completed = [
                item
                for item in followups
                if item.call_line > event.call_line and is_terminal_process_event(item)
            ]
            if completed:
                terminal = completed[-1]
                result = terminal.result
                result_line = terminal.result_line
                action = str(terminal.arguments.get("action") or "").strip().lower()
                if action in {"kill", "remove"}:
                    outcome = "failed"
                outcome = "failed" if action in {"kill", "remove"} else operation_outcome(result)
            else:
                outcome = "pending"
        captured.append(
            {
                "tool_call_id": event.tool_call_id,
                "command": command,
                "call_line": event.call_line,
                "result_line": result_line,
                "outcome": outcome,
            }
        )
    return captured


def _is_dependency_command(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name == "uv" and tokens[index + 1:index + 2] == ["pip"]:
            return True
        if name in {"pip", "pip3"} and tokens[index + 1:index + 2] in (["install"], ["uninstall"]):
            return True
        if name.startswith("python") and tokens[index + 1:index + 3] == ["-m", "pip"]:
            return True
    return False


def remediate_forbidden_distributions(
    environment: AttemptPythonEnvironment,
    manifest: dict[str, Any],
    *,
    run_subprocess: RunSubprocess = subprocess.run,
) -> dict[str, Any]:
    installed = {
        _normalize_distribution(item.get("name"))
        for item in manifest.get("distributions") or []
        if isinstance(item, dict)
    }
    forbidden = sorted(installed & FORBIDDEN_DISTRIBUTIONS)
    report: dict[str, Any] = {
        "status": "clear" if not forbidden else "postinstall_detected",
        "forbidden_distributions": forbidden,
        "uninstall_returncode": None,
    }
    if not forbidden:
        return report
    completed = run_subprocess(
        ["uv", "pip", "uninstall", "--python", str(environment.python), *forbidden],
        cwd=str(environment.venv_dir.parent),
        env={**os.environ, **environment.to_env()},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    report["uninstall_returncode"] = completed.returncode
    report["status"] = "postinstall_removed" if completed.returncode == 0 else "postinstall_removal_failed"
    return report


def _normalize_distribution(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(".", "-")


def binary_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cleanup_attempt_environment(environment: AttemptPythonEnvironment) -> dict[str, Any]:
    report: dict[str, Any] = {"venv_removed": False, "cache_removed": False, "tool_bin_removed": False}
    for key, path in (
        ("venv_removed", environment.venv_dir),
        ("cache_removed", environment.cache_dir),
        ("tool_bin_removed", environment.tool_bin_dir),
    ):
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
        report[key] = not path.exists()
    return report


def cleanup_partial_attempt_environment(root: Path) -> None:
    from benchmarking.runtime.attempt_finalization import cleanup_owned_environment
    report = cleanup_owned_environment(Path(root).expanduser())
    if report["errors"]:
        raise RuntimeError(f"Partial attempt cleanup failed: {report['errors']}")


def _materialize_native_tools(
    tool_bin_dir: Path,
    *,
    uv: str,
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    resolved: dict[str, str] = {"uv": str(Path(uv).expanduser().resolve())}
    for name in ("xtb", "node", "openclaw"):
        candidate = shutil.which(name)
        if candidate:
            resolved[name] = str(Path(candidate).expanduser().resolve())
    for name, target in resolved.items():
        wrapper = tool_bin_dir / name
        if name == "uv" and environment:
            assignments = " ".join(
                f"{key}={shlex.quote(value)}" for key, value in sorted(environment.items())
            )
            content = f"#!/bin/sh\nexec env {assignments} {shlex.quote(target)} \"$@\"\n"
        else:
            content = f'#!/bin/sh\nexec "{target}" "$@"\n'
        wrapper.write_text(content, encoding="utf-8")
        wrapper.chmod(0o500)
    return resolved


def _native_tool_fingerprints(tools: dict[str, str]) -> dict[str, dict[str, str]]:
    fingerprints: dict[str, dict[str, str]] = {}
    for name, raw_path in sorted(tools.items()):
        path = Path(raw_path)
        version = ""
        if path.is_file():
            try:
                completed = subprocess.run(
                    [str(path), "--version"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                version = " ".join((completed.stdout or completed.stderr or "").split())[:500]
            except (OSError, subprocess.TimeoutExpired):
                version = ""
        try:
            digest = binary_sha256(path) if path.is_file() else ""
        except OSError:
            digest = ""
        fingerprints[name] = {
            "path": str(path),
            "sha256": digest,
            "version": version,
        }
    return fingerprints
