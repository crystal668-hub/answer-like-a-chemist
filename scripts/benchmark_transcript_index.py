#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarking.runtime.transcript_index import TranscriptIndex


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def legacy_reads(path: Path) -> int:
    consumed = 0
    for _ in range(3):
        payloads = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                payloads.append(json.loads(line))
        consumed += len(payloads)
    return consumed


def indexed_reads(path: Path) -> int:
    index = TranscriptIndex.from_path(path)
    return len(index.dict_events()) + len(index.messages()) + len(index.strict_payloads())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lines", type=int, required=True)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--mode", choices=("legacy", "indexed"), required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        transcript = Path(directory) / "transcript.jsonl"
        with transcript.open("w", encoding="utf-8") as handle:
            for index in range(args.lines):
                handle.write(json.dumps({
                    "message": {
                        "role": "assistant",
                        "content": [{
                            "type": "text",
                            "text": f"line-{index}:" + "x" * args.payload_bytes,
                        }],
                    }
                }) + "\n")
        started = time.perf_counter()
        consumed = legacy_reads(transcript) if args.mode == "legacy" else indexed_reads(transcript)
        elapsed = time.perf_counter() - started
        print(json.dumps({
            "mode": args.mode,
            "lines": args.lines,
            "payload_bytes": args.payload_bytes,
            "transcript_bytes": transcript.stat().st_size,
            "consumed_views": consumed,
            "elapsed_seconds": elapsed,
            "peak_rss_bytes": peak_rss_bytes(),
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
