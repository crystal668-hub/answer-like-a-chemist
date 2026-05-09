#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarking.skill_runtime import WorkspaceUvSkillRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OpenClaw chemistry skill script through workspace uv.")
    parser.add_argument("--workspace-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--script", required=True, help="Absolute or workspace-relative script path.")
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root).expanduser().resolve()
    script_path = Path(args.script).expanduser()
    if not script_path.is_absolute():
        script_path = (workspace_root / script_path).resolve()
    script_args = list(args.script_args)
    if script_args and script_args[0] == "--":
        script_args = script_args[1:]
    payload = WorkspaceUvSkillRunner(workspace_root=workspace_root).run_script(script_path, script_args)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("available") is not False else 2


if __name__ == "__main__":
    raise SystemExit(main())
