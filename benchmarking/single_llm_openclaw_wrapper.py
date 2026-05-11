#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from benchmarking.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    is_complete_benchmark_answer,
    summarize_transcript_convergence,
)
from benchmarking.openclaw_session_isolation import (
    SessionIsolationError,
    atomic_write_json,
    base_audit,
    entry_matches_requested_session,
    inspect_postflight_session,
    load_json_object,
    load_openclaw_config,
    main_session_keys_for_agent,
    merge_preflight_postflight_audit,
    requested_model_for_agent,
    reset_agent_main_session_if_stale,
    resolve_agent_entry,
    sanitize_agent_id,
    session_store_path_for_agent,
)
from benchmarking.openclaw_env import build_openclaw_subprocess_env
from benchmarking.result_contract import contract_to_payload, parse_agent_stdout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run single-LLM OpenClaw turns with benchmark session isolation.")
    parser.add_argument("--agent", required=True, help="OpenClaw agent id.")
    parser.add_argument("--config-file", required=True, help="OpenClaw config path for this benchmark run.")
    parser.add_argument("--session-id", required=True, help="Run-scoped OpenClaw session id.")
    parser.add_argument("--message", required=True, help="Prompt to send to OpenClaw.")
    parser.add_argument("--thinking", help="Forward OpenClaw thinking override.")
    parser.add_argument("--timeout", type=int, help="Forward OpenClaw timeout override in seconds.")
    parser.add_argument("--finalization-grace-seconds", type=int, default=90)
    parser.add_argument("--json", action="store_true", help="Forward OpenClaw JSON output and attach isolation audit.")
    return parser.parse_args()


def merge_isolation_audit(payload: Any, audit: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        return payload
    target = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if isinstance(target, dict):
        meta = target.setdefault("meta", {})
        if isinstance(meta, dict):
            existing = meta.get("session_isolation")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(audit)
            meta["session_isolation"] = merged
    return payload


def transcript_path_from_audit(audit: dict[str, Any]) -> Path | None:
    raw = str(audit.get("postflight_entry_session_file") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def _target_result_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    return payload


def _is_timeout_like_payload(target: dict[str, Any]) -> bool:
    payloads = target.get("payloads")
    payload_texts: list[str] = []
    if isinstance(payloads, list):
        for item in payloads:
            if isinstance(item, dict):
                payload_texts.append(str(item.get("text") or ""))
    timeout_needles = (
        "Request timed out before a response was generated",
        "The model did not produce a response before the LLM idle timeout",
    )
    for text in payload_texts:
        if any(needle in text for needle in timeout_needles):
            return True
    if any(is_complete_benchmark_answer(text) for text in payload_texts):
        return False
    meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}
    return bool(meta.get("aborted") is True or str(meta.get("livenessState") or "") == "blocked")


def merge_convergence_metadata(payload: Any, *, args: argparse.Namespace, audit: dict[str, Any]) -> Any:
    target = _target_result_payload(payload)
    if target is None:
        return payload
    policy = ConvergencePolicy(
        timeout_seconds=int(getattr(args, "timeout", 0) or 0),
        finalization_grace_seconds=int(getattr(args, "finalization_grace_seconds", 90)),
    )
    convergence_meta: dict[str, Any] = {
        "policy": policy.to_meta(),
        "transcript_answer_recovered": False,
    }
    transcript_path = transcript_path_from_audit(audit)
    if transcript_path is not None:
        convergence_meta.update(summarize_transcript_convergence(transcript_path))

    meta = target.setdefault("meta", {})
    if isinstance(meta, dict):
        existing = meta.get("convergence")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(convergence_meta)
        meta["convergence"] = merged

    if transcript_path is not None and _is_timeout_like_payload(target):
        recovered = extract_latest_complete_answer_from_transcript(transcript_path)
        if recovered:
            target["payloads"] = [{"text": recovered}]
            meta = target.setdefault("meta", {})
            if isinstance(meta, dict):
                convergence = meta.setdefault("convergence", {})
                if isinstance(convergence, dict):
                    convergence["transcript_answer_recovered"] = True
                    convergence["recovery_source"] = "single-llm-session-transcript"
    return payload


def parse_openclaw_json_output(output: str) -> Any:
    return contract_to_payload(parse_agent_stdout(output))


def resolve_openclaw_executable() -> str:
    executable = shutil.which("openclaw")
    if executable:
        return executable
    raise SessionIsolationError("Missing openclaw executable in PATH.")


def run_openclaw(args: argparse.Namespace, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = [
        resolve_openclaw_executable(),
        "agent",
        "--local",
        "--agent",
        args.agent,
        "--session-id",
        args.session_id,
        "--message",
        args.message,
    ]
    if args.thinking:
        command.extend(["--thinking", args.thinking])
    if args.timeout is not None:
        command.extend(["--timeout", str(max(1, int(args.timeout)))])
    if args.json:
        command.append("--json")
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


def main() -> int:
    args = parse_args()
    config_path = Path(args.config_file).expanduser().resolve()
    env = build_openclaw_subprocess_env(base_env=os.environ.copy(), config_path=config_path)
    try:
        preflight_audit = reset_agent_main_session_if_stale(args.agent, args.session_id, config_path=config_path)
        result = run_openclaw(args, env=env)
        if result.returncode != 0:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            return result.returncode
        postflight_audit = inspect_postflight_session(args.agent, args.session_id, config_path=config_path)
        audit = merge_preflight_postflight_audit(preflight_audit, postflight_audit)
        if args.json:
            output = result.stdout.strip() or result.stderr.strip()
            payload = parse_openclaw_json_output(output)
            payload = merge_convergence_metadata(payload, args=args, audit=audit)
            payload = merge_isolation_audit(payload, audit)
            print(json.dumps(payload, ensure_ascii=False))
        else:
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
        return 0
    except Exception as exc:
        if args.json:
            payload = {
                "result": {
                    "payloads": [],
                    "meta": {
                        "session_isolation": {
                            "requested_session_id": args.session_id,
                            "agent_id": args.agent,
                            "session_store_path": "",
                            "preflight_removed_stale_main_entry": False,
                            "preflight_previous_session_id": "",
                            "postflight_entry_session_id": "",
                            "postflight_entry_session_file": "",
                            "session_isolation_ok": False,
                            "error": str(exc),
                        }
                    },
                }
            }
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
