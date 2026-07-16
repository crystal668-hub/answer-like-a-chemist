from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarking.runtime.history_recovery import replay_workspace_adjudication


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay benchmark workspace audit and adjudication without a model call.")
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--record-id", action="append", required=True, dest="record_ids")
    parser.add_argument("--apply", action="store_true", help="Apply eligible replacements; default is dry-run.")
    parser.add_argument("--rescore", action="store_true", help="Run the original registered evaluator.")
    parser.add_argument("--approve-historical-ownership", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = replay_workspace_adjudication(
        run_root=args.run_root,
        group_id=args.group,
        record_ids=args.record_ids,
        apply=args.apply,
        rescore=args.rescore,
        approve_historical_ownership=args.approve_historical_ownership,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
