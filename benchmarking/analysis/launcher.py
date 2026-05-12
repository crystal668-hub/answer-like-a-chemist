from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


DEFAULT_TIMEOUT_SECONDS = 3600


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def analysis_paths(output_root: str | Path) -> dict[str, str]:
    root = Path(output_root).expanduser().resolve()
    analysis_dir = root / "analysis"
    return {
        "analysis_dir": str(analysis_dir),
        "status_path": str(analysis_dir / "status.json"),
        "input_bundle_path": str(analysis_dir / "input-bundle.json"),
        "events_path": str(analysis_dir / "codex-events.jsonl"),
        "report_path": str(analysis_dir / "report.json"),
        "markdown_report_path": str(analysis_dir / "report.md"),
    }


def launch_automated_evaluation(
    output_root: str | Path,
    *,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    python_executable: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    paths = analysis_paths(root)
    status_path = Path(paths["status_path"])
    command = [
        python_executable or sys.executable,
        "-m",
        "benchmarking.automated_evaluation",
        "run",
        "--output-root",
        str(root),
        "--timeout-seconds",
        str(timeout_seconds),
    ]
    base_status: dict[str, Any] = {
        "status": "launching",
        "launched_at": utc_timestamp(),
        "output_root": str(root),
        "command": command,
        **paths,
    }
    save_status(status_path, base_status)
    try:
        process = popen(
            command,
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        failed = {
            **base_status,
            "status": "launch_failed",
            "updated_at": utc_timestamp(),
            "error": f"{type(exc).__name__}: {exc}",
        }
        save_status(status_path, failed)
        return failed
    launched = {
        **base_status,
        "status": "launched",
        "updated_at": utc_timestamp(),
        "pid": int(getattr(process, "pid", 0) or 0),
    }
    save_status(status_path, launched)
    return launched
