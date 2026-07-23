from __future__ import annotations

import inspect
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from benchmarking.runtime.cancellation import CancellationToken, OwnedProcessRegistry


class SubprocessOutputError(RuntimeError):
    pass


def current_python() -> str:
    venv = os.environ.get("VIRTUAL_ENV", "").strip()
    if venv:
        venv_root = Path(venv).expanduser()
        for candidate in (venv_root / "bin" / "python", venv_root / "Scripts" / "python.exe"):
            if candidate.is_file():
                return str(candidate)
    return str(Path(sys.executable).expanduser())


def run_subprocess(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
    cancellation_token: CancellationToken | None = None,
    process_registry: OwnedProcessRegistry | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=process_registry is not None,
    )
    if process_registry is not None:
        process_registry.register(process)
    started = time.monotonic()
    try:
        while True:
            if cancellation_token is not None and cancellation_token.is_cancelled:
                if process_registry is not None:
                    process_registry.terminate_all(force=cancellation_token.request_count > 1)
                elif process.poll() is None:
                    process.terminate()
                process.communicate()
                cancellation_token.raise_if_cancelled()
            remaining = None if timeout is None else max(0.0, timeout - (time.monotonic() - started))
            if remaining is not None and remaining <= 0:
                if process_registry is not None:
                    process_registry.signal_process_group(process, signal.SIGKILL)
                else:
                    process.kill()
                stdout, stderr = process.communicate()
                raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            try:
                wait_seconds = min(0.1, remaining) if remaining is not None else 0.1
                stdout, stderr = process.communicate(timeout=wait_seconds)
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                return subprocess.CompletedProcess(command, process.returncode, stdout=stdout, stderr=stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process_registry is not None:
            process_registry.unregister(process)


def run_owned_subprocess(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: int | None = None,
    cancellation_token: CancellationToken | None = None,
    process_registry: OwnedProcessRegistry | None = None,
) -> subprocess.CompletedProcess[str]:
    parameters = inspect.signature(run_subprocess).parameters
    cancellation_kwargs: dict[str, Any] = {}
    if "cancellation_token" in parameters:
        cancellation_kwargs["cancellation_token"] = cancellation_token
    if "process_registry" in parameters:
        cancellation_kwargs["process_registry"] = process_registry
    return run_subprocess(
        command,
        env=env,
        cwd=cwd,
        timeout=timeout,
        **cancellation_kwargs,
    )


def ensure_success(result: subprocess.CompletedProcess[str], command: list[str]) -> None:
    if result.returncode != 0:
        raise SubprocessOutputError(
            "Command failed\n"
            f"command: {' '.join(command)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def parse_json_stdout(result: subprocess.CompletedProcess[str], command: list[str]) -> Any:
    ensure_success(result, command)
    output = result.stdout.strip()
    if not output:
        raise SubprocessOutputError(f"Empty stdout from command: {' '.join(command)}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise SubprocessOutputError(
            "JSON decode failed\n"
            f"command: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        ) from exc


def deep_copy_jsonish(value: Any) -> Any:
    return json.loads(json.dumps(value))


def unwrap_agent_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        return result
    return payload if isinstance(payload, dict) else {}


def summarize_payloads(payloads: list[dict[str, Any]]) -> str:
    texts = [str(item.get("text") or "").strip() for item in payloads if str(item.get("text") or "").strip()]
    return "\n\n".join(texts).strip()
