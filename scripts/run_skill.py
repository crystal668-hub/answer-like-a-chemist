#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarking.skills.runtime import WorkspaceUvSkillRunner


def _unavailable(error_kind: str, reason: str) -> dict[str, object]:
    return {
        "available": False,
        "error_kind": error_kind,
        "reason": reason,
        "runner": "workspace_uv",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OpenClaw chemistry skill script through workspace uv.")
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--execution-cwd", default=".", help="Working directory for relative skill input/output paths.")
    parser.add_argument("--script", required=True, help="Absolute or workspace-relative script path.")
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    missing_markers = [name for name in ("pyproject.toml", "uv.lock") if not (workspace_root / name).is_file()]
    if missing_markers:
        payload = _unavailable(
            "invalid_workspace_root",
            f"--workspace-root must point to the canonical project root containing pyproject.toml and uv.lock; missing: {', '.join(missing_markers)}",
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    execution_cwd = Path(args.execution_cwd).expanduser().resolve()
    script_path = Path(args.script).expanduser()
    if not script_path.is_absolute():
        script_path = (workspace_root / script_path).resolve()
    script_args = list(args.script_args)
    if script_args and script_args[0] == "--":
        script_args = script_args[1:]
    payload = WorkspaceUvSkillRunner(workspace_root=workspace_root, execution_cwd=execution_cwd).run_script(script_path, script_args)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("available") is not False else 2


if __name__ == "__main__":
    raise SystemExit(main())
