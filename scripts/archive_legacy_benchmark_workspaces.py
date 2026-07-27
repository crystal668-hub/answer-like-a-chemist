from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime.legacy_workspace_archive import archive_legacy_workspaces


DEFAULT_ARCHIVE_ROOT = runtime_paths.project_state_root / "benchmark-runs" / "legacy-workspace-archives"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive complete legacy benchmark workspaces with a verified SHA-256 inventory."
    )
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete every source only after all archives and source snapshots verify.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = archive_legacy_workspaces(
        args.source,
        archive_root=args.archive_root,
        delete_sources=args.delete_source,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
