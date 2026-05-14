#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmarking.core.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    is_complete_benchmark_answer,
    is_complete_rescue_answer,
    summarize_transcript_convergence,
)
from benchmarking.runtime.session_isolation import (
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
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env
from benchmarking.core.result_contract import contract_to_payload, parse_agent_stdout


OPENCLAW_STREAM_READ_ERROR_TEXT = "stream_read_error"
OPENCLAW_AGENT_NO_RESPONSE_FRAGMENT = "Agent couldn't generate a response"
OPENCLAW_TIMEOUT_SENTINELS = (
    "Request timed out before a response was generated",
    "The model did not produce a response before the LLM idle timeout",
    "LLM request timed out.",
    "Request timed out.",
)
TIME_REMINDER_SECONDS = 600
TIME_REMINDER_POLL_SECONDS = 1.0
TIME_REMINDER_PROMPT = """TIME REMINDER:
Less than one third of the answer budget remains. Please quickly organize the reasoning chain already available in this session.
Converge on a complete final answer in the required format.
Do not start new tool chains or skill exploration unless one short decisive check is clearly necessary."""
FINALIZATION_RESCUE_PROMPT = """The previous benchmark turn ended before a visible final answer was emitted.
Do not call tools or inspect files. Use only the reasoning already present in this session.
Provide a brief but complete visible derivation and checks, then provide the final answer in the required format from the benchmark prompt.
For FrontierScience research-track tasks, provide a complete structured research answer and use a clear final/conclusion marker if the original prompt did not require a short final-answer line.
For multiple-choice questions, use: FINAL ANSWER: <option letters>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run single-LLM OpenClaw turns with benchmark session isolation.")
    parser.add_argument("--agent", required=True, help="OpenClaw agent id.")
    parser.add_argument("--config-file", required=True, help="OpenClaw config path for this benchmark run.")
    parser.add_argument("--session-id", required=True, help="Run-scoped OpenClaw session id.")
    parser.add_argument("--message", required=True, help="Prompt to send to OpenClaw.")
    parser.add_argument("--thinking", help="Forward OpenClaw thinking override.")
    parser.add_argument("--timeout", type=int, help="Forward OpenClaw timeout override in seconds.")
    parser.add_argument("--finalization-grace-seconds", type=int, default=90)
    parser.add_argument("--eval-kind", default="", help="Benchmark eval kind for rescue-only answer recovery.")
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
    payload_texts = _payload_texts(target)
    for text in payload_texts:
        if any(needle in text for needle in OPENCLAW_TIMEOUT_SENTINELS):
            return True
    if any(is_complete_benchmark_answer(text) for text in payload_texts):
        return False
    meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}
    return bool(meta.get("aborted") is True or str(meta.get("livenessState") or "") == "blocked")


def _has_timeout_sentinel_payload(target: dict[str, Any]) -> bool:
    return any(any(needle in text for needle in OPENCLAW_TIMEOUT_SENTINELS) for text in _payload_texts(target))


def _payload_texts(target: dict[str, Any]) -> list[str]:
    payloads = target.get("payloads")
    texts: list[str] = []
    if isinstance(payloads, list):
        for item in payloads:
            if isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                if text:
                    texts.append(text)
    return texts


def _has_error_payload_marker(target: dict[str, Any]) -> bool:
    payloads = target.get("payloads")
    if not isinstance(payloads, list):
        return False
    return any(isinstance(item, dict) and item.get("isError") is True for item in payloads)


def _classify_agent_error_payload(target: dict[str, Any]) -> str:
    payload_texts = _payload_texts(target)
    if _has_timeout_sentinel_payload(target):
        return ""
    meta = target.get("meta") if isinstance(target.get("meta"), dict) else {}
    completion = meta.get("completion") if isinstance(meta.get("completion"), dict) else {}
    stop_reason = str(meta.get("stopReason") or "").strip().lower()
    finish_reason = str(completion.get("finishReason") or completion.get("stopReason") or "").strip().lower()
    liveness_state = str(meta.get("livenessState") or "").strip()
    has_complete_answer = any(is_complete_benchmark_answer(text) for text in payload_texts)

    if (
        any(text == OPENCLAW_STREAM_READ_ERROR_TEXT for text in payload_texts)
        or stop_reason == "error"
        or finish_reason == "error"
    ):
        return "agent_stream_read_error"
    if (
        any(OPENCLAW_AGENT_NO_RESPONSE_FRAGMENT in text for text in payload_texts)
        or meta.get("replayInvalid") is True
        or (liveness_state in {"abandoned", "blocked"} and (_has_error_payload_marker(target) or not has_complete_answer))
    ):
        return "agent_response_unavailable"
    return ""


def _session_isolation_ok(audit: dict[str, Any]) -> bool:
    return audit.get("session_isolation_ok") is True


def _merge_convergence(target: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    meta = target.setdefault("meta", {})
    if not isinstance(meta, dict):
        return {}
    existing = meta.get("convergence")
    convergence = dict(existing) if isinstance(existing, dict) else {}
    convergence.update(updates)
    meta["convergence"] = convergence
    return convergence


def _rescue_output_text(payload: Any) -> str:
    target = _target_result_payload(payload)
    if target is None:
        return ""
    return "\n\n".join(_payload_texts(target)).strip()


def _time_reminder_enabled(args: argparse.Namespace) -> bool:
    timeout = int(getattr(args, "timeout", 0) or 0)
    return timeout > TIME_REMINDER_SECONDS


def _base_time_reminder_meta(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "enabled": _time_reminder_enabled(args),
        "threshold_seconds": TIME_REMINDER_SECONDS,
        "due_before_primary_return": False,
        "primary_elapsed_seconds": 0.0,
        "applied": False,
        "skipped_reason": "disabled" if not _time_reminder_enabled(args) else "threshold_not_reached",
        "remaining_seconds_at_primary_return": float(max(0, int(getattr(args, "timeout", 0) or 0))),
    }


def _time_reminder_meta_from_result(result: subprocess.CompletedProcess[str], args: argparse.Namespace) -> dict[str, Any]:
    raw = getattr(result, "time_reminder_meta", None)
    if isinstance(raw, dict):
        meta = _base_time_reminder_meta(args)
        meta.update(raw)
        return meta
    return _base_time_reminder_meta(args)


def _has_complete_answer_in_payload(payload: Any) -> bool:
    target = _target_result_payload(payload)
    if target is None:
        return False
    return any(is_complete_benchmark_answer(text) for text in _payload_texts(target))


def _has_complete_answer_in_result(result: subprocess.CompletedProcess[str], audit: dict[str, Any]) -> bool:
    output = (result.stdout or "").strip() or (result.stderr or "").strip()
    if output:
        try:
            if _has_complete_answer_in_payload(parse_openclaw_json_output(output)):
                return True
        except Exception:
            pass
    transcript_path = transcript_path_from_audit(audit)
    return bool(transcript_path is not None and extract_latest_complete_answer_from_transcript(transcript_path))


def _parse_remaining_seconds(value: Any) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _try_finalization_rescue(
    target: dict[str, Any],
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> bool:
    grace_seconds = max(1, int(getattr(args, "finalization_grace_seconds", 90) or 90))
    try:
        result = run_openclaw(
            args,
            env=env,
            message_override=FINALIZATION_RESCUE_PROMPT,
            timeout_override=grace_seconds,
        )
    except Exception as exc:
        _merge_convergence(
            target,
            {
                "finalization_rescue_attempted": True,
                "finalization_rescue_succeeded": False,
                "finalization_rescue_error": str(exc)[:1000],
            },
        )
        return False

    if result.returncode != 0:
        _merge_convergence(
            target,
            {
                "finalization_rescue_attempted": True,
                "finalization_rescue_succeeded": False,
                "finalization_rescue_returncode": result.returncode,
                "finalization_rescue_stderr_excerpt": str(result.stderr or "")[:1000],
            },
        )
        return False

    rescue_payload = parse_openclaw_json_output((result.stdout or "").strip() or (result.stderr or "").strip())
    rescue_text = _rescue_output_text(rescue_payload)
    if not is_complete_rescue_answer(rescue_text, eval_kind=str(getattr(args, "eval_kind", "") or "")):
        _merge_convergence(
            target,
            {
                "finalization_rescue_attempted": True,
                "finalization_rescue_succeeded": False,
                "finalization_rescue_payload_excerpt": rescue_text[:1000],
            },
        )
        return False

    target["payloads"] = [{"text": rescue_text}]
    _merge_convergence(
        target,
        {
            "finalization_rescue_attempted": True,
            "finalization_rescue_succeeded": True,
            "recovery_source": "single-llm-finalization-rescue",
        },
    )
    return True


def merge_convergence_metadata(
    payload: Any,
    *,
    args: argparse.Namespace,
    audit: dict[str, Any],
    env: dict[str, str] | None = None,
    time_reminder_meta: dict[str, Any] | None = None,
) -> Any:
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
        "agent_error_payload_detected": False,
        "agent_error_kind": "",
        "finalization_rescue_attempted": False,
        "finalization_rescue_succeeded": False,
        "time_reminder": dict(time_reminder_meta or _base_time_reminder_meta(args)),
    }
    transcript_path = transcript_path_from_audit(audit)
    if transcript_path is not None:
        convergence_meta.update(summarize_transcript_convergence(transcript_path))
    agent_error_kind = _classify_agent_error_payload(target)
    if agent_error_kind:
        convergence_meta["agent_error_payload_detected"] = True
        convergence_meta["agent_error_kind"] = agent_error_kind

    meta = target.setdefault("meta", {})
    if isinstance(meta, dict):
        existing = meta.get("convergence")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(convergence_meta)
        meta["convergence"] = merged

    error_like = bool(agent_error_kind)
    timeout_like = _is_timeout_like_payload(target)
    if transcript_path is not None and (timeout_like or error_like):
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
    if (
        error_like
        and env is not None
        and transcript_path is not None
        and _session_isolation_ok(audit)
        and not any(is_complete_benchmark_answer(text) for text in _payload_texts(target))
    ):
        _try_finalization_rescue(target, args=args, env=env)
    return payload


def parse_openclaw_json_output(output: str) -> Any:
    return contract_to_payload(parse_agent_stdout(output))


def resolve_openclaw_executable() -> str:
    executable = shutil.which("openclaw")
    if executable:
        return executable
    raise SessionIsolationError("Missing openclaw executable in PATH.")


def _build_openclaw_command(
    args: argparse.Namespace,
    *,
    message_override: str | None = None,
    timeout_override: int | None = None,
) -> list[str]:
    timeout = args.timeout if timeout_override is None else timeout_override
    command = [
        resolve_openclaw_executable(),
        "agent",
        "--local",
        "--agent",
        args.agent,
        "--session-id",
        args.session_id,
        "--message",
        args.message if message_override is None else message_override,
    ]
    if args.thinking:
        command.extend(["--thinking", args.thinking])
    if timeout is not None:
        command.extend(["--timeout", str(max(1, int(timeout)))])
    if args.json:
        command.append("--json")
    return command


def _run_openclaw_with_time_reminder_tracking(
    command: list[str],
    *,
    args: argparse.Namespace,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    timeout_seconds = int(getattr(args, "timeout", 0) or 0)
    start = time.monotonic()
    reminder_due = False
    proc = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=TIME_REMINDER_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - start
            if not reminder_due and elapsed >= TIME_REMINDER_SECONDS:
                reminder_due = True
    elapsed = time.monotonic() - start
    reminder_due = reminder_due or elapsed >= TIME_REMINDER_SECONDS
    remaining = max(0.0, float(timeout_seconds) - elapsed) if timeout_seconds > 0 else 0.0
    result = subprocess.CompletedProcess(command, proc.returncode, stdout=stdout, stderr=stderr)
    result.time_reminder_meta = {
        "enabled": True,
        "threshold_seconds": TIME_REMINDER_SECONDS,
        "due_before_primary_return": reminder_due,
        "primary_elapsed_seconds": elapsed,
        "applied": False,
        "skipped_reason": "" if reminder_due else "threshold_not_reached",
        "remaining_seconds_at_primary_return": remaining,
    }
    return result


def run_openclaw(
    args: argparse.Namespace,
    *,
    env: dict[str, str],
    message_override: str | None = None,
    timeout_override: int | None = None,
) -> subprocess.CompletedProcess[str]:
    command = _build_openclaw_command(args, message_override=message_override, timeout_override=timeout_override)
    if message_override is None and timeout_override is None and _time_reminder_enabled(args):
        return _run_openclaw_with_time_reminder_tracking(command, args=args, env=env)
    return subprocess.run(command, env=env, capture_output=True, text=True, check=False)


def _maybe_run_time_reminder(
    primary_result: subprocess.CompletedProcess[str],
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    audit: dict[str, Any],
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    reminder_meta = _time_reminder_meta_from_result(primary_result, args)
    if not reminder_meta.get("enabled"):
        reminder_meta["skipped_reason"] = "disabled"
        return primary_result, reminder_meta
    if not reminder_meta.get("due_before_primary_return"):
        reminder_meta["skipped_reason"] = "threshold_not_reached"
        return primary_result, reminder_meta
    if _has_complete_answer_in_result(primary_result, audit):
        reminder_meta["skipped_reason"] = "complete_answer_available"
        return primary_result, reminder_meta
    remaining_seconds = _parse_remaining_seconds(reminder_meta.get("remaining_seconds_at_primary_return"))
    if remaining_seconds <= 0:
        reminder_meta["skipped_reason"] = "no_remaining_time"
        return primary_result, reminder_meta

    reminder_result = run_openclaw(
        args,
        env=env,
        message_override=TIME_REMINDER_PROMPT,
        timeout_override=remaining_seconds,
    )
    reminder_meta["applied"] = True
    reminder_meta["skipped_reason"] = ""
    if reminder_result.returncode != 0:
        reminder_meta["reminder_returncode"] = reminder_result.returncode
        reminder_meta["reminder_stderr_excerpt"] = str(reminder_result.stderr or "")[:1000]
        return primary_result, reminder_meta
    return reminder_result, reminder_meta


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
        primary_postflight_audit = inspect_postflight_session(args.agent, args.session_id, config_path=config_path)
        primary_audit = merge_preflight_postflight_audit(preflight_audit, primary_postflight_audit)
        result, time_reminder_meta = _maybe_run_time_reminder(result, args=args, env=env, audit=primary_audit)
        postflight_audit = inspect_postflight_session(args.agent, args.session_id, config_path=config_path)
        audit = merge_preflight_postflight_audit(preflight_audit, postflight_audit)
        if args.json:
            output = result.stdout.strip() or result.stderr.strip()
            payload = parse_openclaw_json_output(output)
            payload = merge_convergence_metadata(payload, args=args, audit=audit, env=env, time_reminder_meta=time_reminder_meta)
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
