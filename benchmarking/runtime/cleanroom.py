from __future__ import annotations

import atexit
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class CleanroomError(RuntimeError):
    pass


CLEANROOM_REGISTRY_LOCK = threading.Lock()
CLEANROOM_PENDING_MANIFESTS: dict[str, Path] = {}
CLEANROOM_HOOKS_INSTALLED = False


def load_cleanroom_runtime_lease_module(module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("benchmark_cleanroom_runtime_lease", module_path)
    if spec is None or spec.loader is None:
        raise CleanroomError(f"Unable to load benchmark cleanroom runtime_lease.py from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def require_cleanroom_runtime_lease(cleanroom_runtime_lease: Any, *, cleanroom_root: Path) -> Any:
    if cleanroom_runtime_lease is None:
        raise CleanroomError(f"benchmark-cleanroom runtime helpers are unavailable under {cleanroom_root}")
    return cleanroom_runtime_lease


def cleanup_manifest_path(
    output_root: Path,
    run_id: str,
    *,
    cleanroom_runtime_lease: Any,
    cleanroom_root: Path,
) -> Path:
    module = require_cleanroom_runtime_lease(cleanroom_runtime_lease, cleanroom_root=cleanroom_root)
    return module.manifest_path(output_root, run_id)


def build_cleanup_manifest_payload(
    *,
    run_id: str,
    benchmark_kind: str,
    group_id: str,
    output_root: Path,
    cleanroom_runtime_lease: Any,
    cleanroom_root: Path,
    launch_home: Path | None = None,
    clawteam_data_dir: Path | None = None,
    session_assignments: dict[str, str] | None = None,
    control_roots: list[Path] | None = None,
    generated_roots: list[Path] | None = None,
    artifact_roots: list[Path] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    module = require_cleanroom_runtime_lease(cleanroom_runtime_lease, cleanroom_root=cleanroom_root)
    lease_dir = output_root / "cleanroom" / "leases"
    return module.build_manifest_payload(
        run_id=run_id,
        benchmark_kind=benchmark_kind,
        group_id=group_id,
        output_root=output_root,
        launch_home=launch_home or "",
        clawteam_data_dir=clawteam_data_dir or "",
        session_assignments=session_assignments or {},
        control_roots=[str(path) for path in (control_roots or [])],
        generated_roots=[str(path) for path in (generated_roots or [])],
        artifact_roots=[str(path) for path in (artifact_roots or [])],
        lease_dir=lease_dir,
        extra=extra or {},
    )


def write_cleanup_manifest(
    path: Path,
    payload: dict[str, Any],
    *,
    cleanroom_runtime_lease: Any,
    cleanroom_root: Path,
) -> Path:
    module = require_cleanroom_runtime_lease(cleanroom_runtime_lease, cleanroom_root=cleanroom_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    return module.write_manifest(path, payload)


def update_cleanup_manifest(
    path: Path,
    patch: dict[str, Any],
    *,
    cleanroom_runtime_lease: Any,
    cleanroom_root: Path,
) -> dict[str, Any]:
    module = require_cleanroom_runtime_lease(cleanroom_runtime_lease, cleanroom_root=cleanroom_root)
    return module.update_manifest(path, patch)


def register_pending_cleanup_manifest(path: Path, *, cleanup_callback: Callable[[], list[dict[str, Any]]]) -> None:
    global CLEANROOM_HOOKS_INSTALLED
    with CLEANROOM_REGISTRY_LOCK:
        CLEANROOM_PENDING_MANIFESTS[str(path)] = path
        if CLEANROOM_HOOKS_INSTALLED:
            return
        atexit.register(cleanup_callback)
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _cleanroom_signal_handler(cleanup_callback))
            except Exception:
                continue
        CLEANROOM_HOOKS_INSTALLED = True


def unregister_pending_cleanup_manifest(path: Path) -> None:
    with CLEANROOM_REGISTRY_LOCK:
        CLEANROOM_PENDING_MANIFESTS.pop(str(path), None)


def iter_pending_cleanup_manifests() -> list[Path]:
    with CLEANROOM_REGISTRY_LOCK:
        return list(CLEANROOM_PENDING_MANIFESTS.values())


def _cleanroom_signal_handler(cleanup_callback: Callable[[], list[dict[str, Any]]]) -> Callable[[int, Any], None]:
    def handle(signum: int, _frame: Any) -> None:
        try:
            cleanup_callback()
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    return handle


def parse_json_stdout(result: subprocess.CompletedProcess[str], command: list[str]) -> Any:
    if result.returncode != 0:
        raise CleanroomError(
            "Command failed\n"
            f"command: {' '.join(command)}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    output = result.stdout.strip() or result.stderr.strip()
    if not output:
        raise CleanroomError(f"Empty stdout/stderr from command: {' '.join(command)}")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CleanroomError(
            "JSON decode failed\n"
            f"command: {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        ) from exc


def invoke_cleanroom_cleanup(
    *,
    manifest_path: Path,
    cleanroom_root: Path,
    current_python: Callable[[], str],
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]],
    grace_seconds: float = 5.0,
    kill_after_seconds: float = 10.0,
) -> dict[str, Any]:
    cleanup_script = cleanroom_root / "scripts" / "cleanup_benchmark_run.py"
    command = [
        current_python(),
        str(cleanup_script),
        "--manifest",
        str(manifest_path),
        "--grace-seconds",
        str(grace_seconds),
        "--kill-after-seconds",
        str(kill_after_seconds),
        "--json",
    ]
    result = run_subprocess(command, timeout=max(30, int(grace_seconds + kill_after_seconds + 20)))
    payload = parse_json_stdout(result, command)
    if not isinstance(payload, dict):
        raise CleanroomError(f"benchmark cleanroom cleanup did not return an object for manifest `{manifest_path}`")
    if not payload.get("success"):
        raise CleanroomError(f"benchmark cleanroom cleanup failed for `{manifest_path}`: {payload}")
    return payload


def run_pending_cleanroom_cleanup(
    *,
    invoke_cleanup: Callable[[Path], dict[str, Any]],
    unregister: Callable[[Path], None] = unregister_pending_cleanup_manifest,
    iter_pending: Callable[[], list[Path]] = iter_pending_cleanup_manifests,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for manifest_path in iter_pending():
        try:
            report = invoke_cleanup(manifest_path)
            reports.append(report)
        except Exception:
            continue
        finally:
            unregister(manifest_path)
    return reports


@dataclass(frozen=True)
class CleanroomRuntime:
    cleanroom_root: Path
    runtime_lease: Any
    current_python: Callable[[], str]
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]]

    @classmethod
    def load(
        cls,
        *,
        cleanroom_root: Path,
        current_python: Callable[[], str],
        run_subprocess: Callable[..., subprocess.CompletedProcess[str]],
    ) -> CleanroomRuntime:
        module_path = cleanroom_root / "scripts" / "runtime_lease.py"
        runtime_lease = load_cleanroom_runtime_lease_module(module_path)
        return cls(
            cleanroom_root=cleanroom_root,
            runtime_lease=runtime_lease,
            current_python=current_python,
            run_subprocess=run_subprocess,
        )

    def cleanup_manifest_path(self, output_root: Path, run_id: str) -> Path:
        return cleanup_manifest_path(
            output_root,
            run_id,
            cleanroom_runtime_lease=self.runtime_lease,
            cleanroom_root=self.cleanroom_root,
        )

    def build_cleanup_manifest_payload(self, **kwargs: Any) -> dict[str, Any]:
        return build_cleanup_manifest_payload(
            **kwargs,
            cleanroom_runtime_lease=self.runtime_lease,
            cleanroom_root=self.cleanroom_root,
        )

    def write_cleanup_manifest(self, path: Path, payload: dict[str, Any]) -> Path:
        return write_cleanup_manifest(
            path,
            payload,
            cleanroom_runtime_lease=self.runtime_lease,
            cleanroom_root=self.cleanroom_root,
        )

    def update_cleanup_manifest(self, path: Path, patch: dict[str, Any]) -> dict[str, Any]:
        return update_cleanup_manifest(
            path,
            patch,
            cleanroom_runtime_lease=self.runtime_lease,
            cleanroom_root=self.cleanroom_root,
        )

    def register_pending_cleanup_manifest(self, path: Path) -> None:
        register_pending_cleanup_manifest(path, cleanup_callback=self.run_pending_cleanroom_cleanup)

    def unregister_pending_cleanup_manifest(self, path: Path) -> None:
        unregister_pending_cleanup_manifest(path)

    def invoke_cleanroom_cleanup(
        self,
        manifest_path: Path,
        *,
        grace_seconds: float = 5.0,
        kill_after_seconds: float = 10.0,
    ) -> dict[str, Any]:
        return invoke_cleanroom_cleanup(
            manifest_path=manifest_path,
            cleanroom_root=self.cleanroom_root,
            current_python=self.current_python,
            run_subprocess=self.run_subprocess,
            grace_seconds=grace_seconds,
            kill_after_seconds=kill_after_seconds,
        )

    def run_pending_cleanroom_cleanup(self) -> list[dict[str, Any]]:
        return run_pending_cleanroom_cleanup(
            invoke_cleanup=self.invoke_cleanroom_cleanup,
        )
