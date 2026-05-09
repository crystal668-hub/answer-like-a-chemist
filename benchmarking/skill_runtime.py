from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


RunSubprocess = Callable[..., subprocess.CompletedProcess[str]]


MODULE_NOT_FOUND_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")


@dataclass(frozen=True)
class WorkspaceUvSkillRunner:
    workspace_root: Path
    uv_executable: str | None = None
    run_subprocess: RunSubprocess = subprocess.run
    timeout_seconds: int = 60

    def resolved_uv(self) -> str:
        executable = self.uv_executable or shutil.which("uv")
        if not executable:
            raise FileNotFoundError("uv executable not found in PATH")
        return executable

    def build_command(self, script_path: Path, args: list[str]) -> list[str]:
        return [self.resolved_uv(), "run", "python", str(script_path), *args]

    def run_script(self, script_path: Path, args: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
        command = self.build_command(script_path, args)
        merged_env = os.environ.copy()
        merged_env["PYTHONNOUSERSITE"] = "1"
        merged_env["OPENCLAW_SKILL_RUNNER"] = "workspace_uv"
        if env:
            merged_env.update(env)
        try:
            completed = self.run_subprocess(
                command,
                cwd=str(self.workspace_root),
                env=merged_env,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError as exc:
            return _unavailable("missing_executable", str(exc), command=command)
        except subprocess.TimeoutExpired:
            return _unavailable("provider_failure", f"skill script timed out after {self.timeout_seconds}s", command=command)

        if completed.returncode != 0:
            return classify_skill_process_failure(
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                command=command,
            )
        try:
            payload = json.loads((completed.stdout or "").strip())
        except json.JSONDecodeError:
            return _unavailable(
                "invalid_output",
                "skill script completed but did not emit a JSON object",
                command=command,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        if not isinstance(payload, dict):
            return _unavailable("invalid_output", "skill script JSON output was not an object", command=command)
        payload.setdefault("available", True)
        payload.setdefault("runner", "workspace_uv")
        payload.setdefault("command", command)
        return payload


def classify_skill_process_failure(*, returncode: int, stdout: str, stderr: str, command: list[str]) -> dict[str, Any]:
    combined = f"{stdout}\n{stderr}"
    missing = MODULE_NOT_FOUND_RE.search(combined)
    if missing:
        return _unavailable(
            "missing_dependency",
            f"missing Python module: {missing.group(1)}",
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
    if any(token in combined for token in ("403", "401", "HTTPError", "SSLError", "ConnectionError", "fetch failed")):
        return _unavailable(
            "provider_failure",
            _single_line(combined) or "external provider request failed",
            command=command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )
    return _unavailable(
        "script_error",
        _single_line(combined) or f"skill script exited with return code {returncode}",
        command=command,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _unavailable(
    error_kind: str,
    reason: str,
    *,
    command: list[str],
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": False,
        "error_kind": error_kind,
        "reason": reason,
        "runner": "workspace_uv",
        "command": command,
    }
    if returncode is not None:
        payload["returncode"] = returncode
    if stdout:
        payload["stdout_excerpt"] = stdout[:2000]
    if stderr:
        payload["stderr_excerpt"] = stderr[:2000]
    return payload


def _single_line(text: str) -> str:
    return " ".join(str(text or "").split())[:500]
