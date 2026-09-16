#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarking.runtime.agent_workspace import AttemptWorkspaceManager, _symlink_stats, _tree_stats
from benchmarking.runtime.atomic_io import atomic_write_json, atomic_write_text
from benchmarking.runtime.observability import finish_runtime_metrics, start_runtime_metrics


def build_tree(root: Path, files: int, bytes_per_file: int) -> None:
    scratch = root / "scratch"
    scratch.mkdir(parents=True)
    (root / ".benchmark-workspace.json").write_text("{}", encoding="utf-8")
    content = b"x" * bytes_per_file
    for index in range(files):
        path = scratch / f"d{index % 100:03d}" / f"f{index:06d}.bin"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    for index in range(min(100, files)):
        (scratch / f"link-{index:03d}").symlink_to(f"d{index % 100:03d}/f{index:06d}.bin")
    (scratch / "dangling").symlink_to("missing/target")


def measure(mode: str, files: int, bytes_per_file: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="archive-inventory-") as temporary:
        root = Path(temporary) / "workspace"
        build_tree(root, files, bytes_per_file)
        copied = Path(temporary) / "copied"
        if mode.endswith("-cross"):
            shutil.copytree(root, copied, symlinks=True)
        metrics = start_runtime_metrics()
        started = time.perf_counter()
        if mode == "legacy":
            first = AttemptWorkspaceManager._validate_runtime_tree(root)
            second = AttemptWorkspaceManager._validate_runtime_tree(root)
            tree = _tree_stats(root)
            links = _symlink_stats(root)
        elif mode == "consolidated":
            first = AttemptWorkspaceManager._validate_runtime_tree(root)
            second = AttemptWorkspaceManager._validate_runtime_tree(root)
            tree = second.tree_stats()
            links = second.symlink_stats()
        elif mode == "legacy-cross":
            first = AttemptWorkspaceManager._validate_runtime_tree(root)
            assert _tree_stats(root) == _tree_stats(copied)
            assert _symlink_stats(root) == _symlink_stats(copied)
            second = AttemptWorkspaceManager._validate_runtime_tree(copied)
            tree = _tree_stats(copied)
            links = _symlink_stats(copied)
        else:
            first = AttemptWorkspaceManager._validate_runtime_tree(root)
            sentinel_sha256 = hashlib.sha256(b"{}").hexdigest()
            second = AttemptWorkspaceManager._validate_copied_workspace(
                root, copied, sentinel_sha256, expected_source_inventory=first
            )
            tree = second.tree_stats()
            links = second.symlink_stats()
        elapsed = time.perf_counter() - started
        snapshot = finish_runtime_metrics(metrics)
        assert first.tree_stats() == tree and first.symlink_stats() == links
        return {"mode": mode, "files": files, "bytes_per_file": bytes_per_file,
                "wall_seconds": elapsed, "tree_stats": tree, "symlink_stats": links,
                "traversal_count": snapshot["counters"].get("workspace_inventory_traversal_count", 0),
                "entry_count": snapshot["counters"].get("workspace_inventory_entry_count", 0)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--bytes-per-file", type=int, default=1024)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mode", choices=("legacy", "consolidated", "legacy-cross", "consolidated-cross"))
    args = parser.parse_args()
    if args.mode:
        print(json.dumps(measure(args.mode, args.files, args.bytes_per_file), sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required for a matrix run")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    raw = []
    for repeat in range(args.repeats):
        base_modes = ("legacy", "consolidated") if repeat % 2 == 0 else ("consolidated", "legacy")
        cross_modes = ("legacy-cross", "consolidated-cross") if repeat % 2 == 0 else ("consolidated-cross", "legacy-cross")
        modes = (*base_modes, *cross_modes)
        for mode in modes:
            command = [sys.executable, str(Path(__file__).resolve()), "--mode", mode,
                       "--files", str(args.files), "--bytes-per-file", str(args.bytes_per_file)]
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            raw.append({"repeat": repeat + 1, "mode": mode, "command": command,
                        "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr})
            atomic_write_text(output / "raw-results.jsonl",
                              "".join(json.dumps(item, sort_keys=True) + "\n" for item in raw))
            if completed.returncode:
                return completed.returncode
            row = json.loads(completed.stdout)
            row["repeat"] = repeat + 1
            rows.append(row)
    signatures = {(tuple(row["tree_stats"]), tuple(row["symlink_stats"])) for row in rows}
    report = {"schema_version": 1, "status": "passed" if len(signatures) == 1 else "failed",
              "files": args.files, "bytes_per_file": args.bytes_per_file, "repeats": args.repeats,
              "measurements": rows, "medians": {
                  mode: {key: statistics.median(row[key] for row in rows if row["mode"] == mode)
                         for key in ("wall_seconds", "traversal_count", "entry_count")}
                  for mode in ("legacy", "consolidated", "legacy-cross", "consolidated-cross")}}
    atomic_write_json(output / "report.json", report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
