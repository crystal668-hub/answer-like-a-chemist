from __future__ import annotations

import json
import math
import os
import re
import shlex
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from benchmarking.runtime.atomic_io import atomic_write_json
from benchmarking.runtime.transcript_index import TranscriptIndex
from benchmarking.runtime.transcript_tools import (
    ToolEvent,
    background_session_id,
    canonical_tool_status,
    is_terminal_process_event,
    raw_text_sha256,
    redact_observability_text,
    result_exit_code,
    tool_events_from_transcript,
    tool_result_text,
)

OBSERVABILITY_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 5
DEPENDENCY_MANIFEST_SCHEMA_VERSION = 3
RESOURCE_WINDOW_SECONDS = 5.0
MAX_RECORDED_EXEC_CALLS = 1000
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(value: str, *, limit: int = 80) -> str:
    raw = str(value or "")
    cleaned = _SLUG_RE.sub("-", raw).strip("-._") or "unknown"
    if cleaned == raw and len(cleaned) <= limit:
        return cleaned
    digest = raw_text_sha256(raw)[:8]
    return f"{cleaned[: max(1, limit - 9)]}-{digest}"


def attempt_artifact_paths(output_root: Path, identity: Mapping[str, Any]) -> dict[str, Path]:
    session_id = str(identity.get("session_id") or "")
    session_hash = raw_text_sha256(session_id)[:8]
    attempt_index = int(identity.get("attempt_index") or 0)
    root = (
        Path(output_root)
        / "observability"
        / "attempts"
        / _slug(str(identity.get("group_id") or ""))
        / _slug(str(identity.get("record_id") or ""))
        / f"attempt-{attempt_index}-{session_hash}"
    )
    active = Path(output_root) / "observability" / "active" / f"{raw_text_sha256('/'.join(str(identity.get(key) or '') for key in ('group_id', 'record_id', 'attempt_index', 'session_id')))[:20]}.json"
    return {
        "root": root,
        "summary": root / "summary.json",
        "resources": root / "resources.jsonl",
        "active": active,
    }


def relative_artifact_path(output_root: Path, path: Path) -> str:
    return str(path.resolve(strict=False).relative_to(Path(output_root).resolve(strict=False)))


def publish_active_attempt(
    output_root: Path,
    identity: Mapping[str, Any],
    *,
    started_at: str,
    resources_path: Path,
) -> Path:
    paths = attempt_artifact_paths(output_root, identity)
    payload = {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "status": "running",
        "identity": dict(identity),
        "started_at": started_at,
        "owner_pid": os.getpid(),
        "resources_path": relative_artifact_path(output_root, resources_path),
    }
    atomic_write_json(paths["active"], payload)
    return paths["active"]


def clear_active_attempt(path: Path) -> None:
    with suppress(OSError):
        path.unlink()


def _usage_payload(raw: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    def number(*names: str) -> int:
        for name in names:
            value = raw.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                return max(0, int(value))
        return 0

    input_tokens = number("input", "input_tokens", "inputTokens")
    output_tokens = number("output", "output_tokens", "outputTokens")
    cache_read_tokens = number("cacheRead", "cache_read_tokens", "cacheReadTokens")
    cache_write_tokens = number("cacheWrite", "cache_write_tokens", "cacheWriteTokens")
    reasoning_tokens = number("reasoningTokens", "reasoning_tokens")
    component_total = input_tokens + output_tokens + cache_read_tokens + cache_write_tokens
    reported_total = number("total", "totalTokens", "total_tokens")
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": component_total,
        "provider_reported_total_tokens": reported_total or None,
        "provider_total_consistent": reported_total in (0, component_total),
        "source": source,
    }


def token_usage_from_trajectory(path: str | Path) -> dict[str, Any] | None:
    trajectory = Path(path).expanduser()
    if trajectory.is_symlink() or not trajectory.is_file():
        return None
    try:
        events = TranscriptIndex.from_path(trajectory, errors="replace").dict_events()
    except (OSError, UnicodeError):
        return None
    for event in reversed(events):
        if event.get("type") != "trace.artifacts":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else None
        if usage:
            result = _usage_payload(usage, source="trajectory_final")
            result["trajectory_path"] = str(trajectory)
            return result
    return None


def token_usage_from_transcript(index: TranscriptIndex) -> dict[str, Any] | None:
    totals = Counter()
    call_count = 0
    for message in index.messages():
        if str(message.get("role") or "").lower() != "assistant":
            continue
        usage = message.get("usage") if isinstance(message.get("usage"), Mapping) else None
        if not usage:
            continue
        normalized = _usage_payload(usage, source="transcript_messages")
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "total_tokens",
        ):
            totals[key] += int(normalized[key])
        call_count += 1
    if not call_count:
        return None
    return {
        **dict(totals),
        "provider_reported_total_tokens": None,
        "provider_total_consistent": True,
        "source": "transcript_messages",
        "model_call_count": call_count,
        "transcript_path": str(index.path),
    }


def aggregate_token_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    return {
        **{key: sum(int(item.get(key) or 0) for item in usages) for key in keys},
        "invocation_count": len(usages),
        "provider_total_consistent": all(item.get("provider_total_consistent") is not False for item in usages),
    }


def _event_duration_ms(event: ToolEvent, result: Mapping[str, Any] | None) -> tuple[int | None, str]:
    details = result.get("details") if isinstance(result, Mapping) and isinstance(result.get("details"), Mapping) else {}
    duration = details.get("durationMs", details.get("duration_ms"))
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        return int(duration), "tool_result"
    if event.call_timestamp_ms is not None and event.result_timestamp_ms is not None:
        return max(0, event.result_timestamp_ms - event.call_timestamp_ms), "transcript_timestamps"
    return None, "unavailable"


def _background_terminal_event(event: ToolEvent, events: list[ToolEvent]) -> ToolEvent | None:
    session_id = background_session_id(event)
    if not session_id:
        return None
    candidates = []
    for candidate in events:
        if candidate.call_line <= event.call_line or candidate.tool_name.strip().lower() != "process":
            continue
        arguments = candidate.arguments if isinstance(candidate.arguments, Mapping) else {}
        observed = str(arguments.get("sessionId") or arguments.get("session_id") or "").strip()
        if observed == session_id and is_terminal_process_event(candidate):
            candidates.append(candidate)
    return candidates[-1] if candidates else None


def _skill_runner_call(command: str) -> bool:
    return "BENCHMARK_SKILL_RUNNER" in command or "scripts/run_skill.py" in command


def summarize_tool_events(
    index: TranscriptIndex,
    *,
    path_replacements: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payloads = [(entry.line_number, entry.payload) for entry in index.entries]
    events, standalone = tool_events_from_transcript(payloads)
    status_counts: Counter[str] = Counter()
    by_name: Counter[str] = Counter()
    exec_calls: list[dict[str, Any]] = []
    skill_runner_calls = 0
    for sequence, event in enumerate(events, start=1):
        name = event.tool_name.strip().lower() or "unknown"
        background_id = background_session_id(event)
        result = event.result
        result_line = event.result_line
        result_timestamp_ms = event.result_timestamp_ms
        terminal = _background_terminal_event(event, events) if name == "exec" else None
        if terminal is not None:
            result = terminal.result
            result_line = terminal.result_line
            result_timestamp_ms = terminal.result_timestamp_ms
            event = ToolEvent(
                tool_call_id=event.tool_call_id,
                tool_name=event.tool_name,
                arguments=event.arguments,
                call_line=event.call_line,
                call_timestamp_ms=event.call_timestamp_ms,
                result_line=result_line,
                result_timestamp_ms=result_timestamp_ms,
                result=result,
            )
        status, failure_kind = canonical_tool_status(result)
        status_counts[status] += 1
        by_name[name] += 1
        if name != "exec":
            continue
        arguments = event.arguments if isinstance(event.arguments, Mapping) else {}
        command = str(arguments.get("command") or "")
        if _skill_runner_call(command):
            skill_runner_calls += 1
        duration_ms, duration_source = _event_duration_ms(event, result)
        details = result.get("details") if isinstance(result, Mapping) and isinstance(result.get("details"), Mapping) else {}
        requested_cwd = arguments.get("workdir", arguments.get("cwd", ""))
        effective_cwd = details.get("cwd", "")
        exec_calls.append(
            {
                "sequence": sequence,
                "tool_call_id": event.tool_call_id,
                "command": redact_observability_text(command, path_replacements=path_replacements),
                "command_sha256": raw_text_sha256(command),
                "requested_cwd": redact_observability_text(requested_cwd, path_replacements=path_replacements),
                "effective_cwd": redact_observability_text(effective_cwd, path_replacements=path_replacements),
                "call_line": event.call_line,
                "result_line": result_line,
                "started_at_ms": event.call_timestamp_ms,
                "ended_at_ms": result_timestamp_ms,
                "duration_ms": duration_ms,
                "duration_source": duration_source,
                "status": status,
                "failure_kind": failure_kind or None,
                "exit_code": result_exit_code(result),
                "background_session_id": background_id or None,
                "result_excerpt": redact_observability_text(
                    tool_result_text(result), path_replacements=path_replacements, limit=1000
                ),
            }
        )
    failures = len(events) - status_counts.get("success", 0)
    exec_failures = sum(item["status"] != "success" for item in exec_calls)
    recorded_exec_calls = exec_calls[:MAX_RECORDED_EXEC_CALLS]
    transcript_meta = index.to_meta()
    transcript_meta["parse_failures"] = transcript_meta.get("parse_failures", [])[:20]
    return {
        "coverage": "exact" if not index.parse_failures else "partial",
        "call_count": len(events),
        "failure_count": failures,
        "failure_rate": failures / len(events) if events else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "by_name": dict(sorted(by_name.items())),
        "exec_call_count": len(exec_calls),
        "exec_failure_count": exec_failures,
        "exec_failure_rate": exec_failures / len(exec_calls) if exec_calls else 0.0,
        "skill_runner_call_count": skill_runner_calls,
        "exec_calls": recorded_exec_calls,
        "exec_calls_truncated": max(0, len(exec_calls) - len(recorded_exec_calls)),
        "standalone_result_count": len(standalone),
        "transcript": transcript_meta,
    }


def distribution_map(rows: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        version = str(row.get("version") or "").strip()
        if name and version:
            result[re.sub(r"[-_.]+", "-", name).lower()] = version
    return result


def package_delta(baseline_rows: Any, observed_rows: Any, effective_rows: Any = None) -> dict[str, Any]:
    baseline = distribution_map(baseline_rows)
    observed = distribution_map(observed_rows)
    effective = distribution_map(effective_rows if effective_rows is not None else observed_rows)
    baseline_available = isinstance(baseline_rows, list)
    observed_available = isinstance(observed_rows, list)
    effective_available = isinstance(effective_rows, list) if effective_rows is not None else observed_available
    added = ([{"name": name, "version": observed[name]} for name in sorted(observed.keys() - baseline.keys())] if baseline_available and observed_available else [])
    removed = ([{"name": name, "version": baseline[name]} for name in sorted(baseline.keys() - observed.keys())] if baseline_available and observed_available else [])
    changed = ([
        {"name": name, "from_version": baseline[name], "to_version": observed[name]}
        for name in sorted(baseline.keys() & observed.keys())
        if baseline[name] != observed[name]
    ] if baseline_available and observed_available else [])
    policy_removed = ([
        {"name": name, "version": observed[name]}
        for name in sorted(observed.keys() - effective.keys())
    ] if observed_available and effective_available else [])
    return {
        "added": added,
        "removed": removed,
        "version_changed": changed,
        "policy_removed": policy_removed,
        "baseline_count": len(baseline),
        "observed_count": len(observed),
        "effective_count": len(effective),
    }


def package_summary(manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(manifest, Mapping) or not manifest:
        return {
            "coverage": "unavailable",
            "install_events": [],
            "delta": package_delta([], []),
        }
    baseline = manifest.get("baseline_distributions")
    observed = manifest.get("distributions")
    effective = manifest.get("effective_distributions")
    coverage = (
        "exact"
        if isinstance(baseline, list)
        and isinstance(observed, list)
        and isinstance(effective, list)
        else "partial"
    )
    delta = package_delta(baseline, observed, effective)
    events = [dict(item) for item in manifest.get("install_events", []) if isinstance(item, Mapping)]
    failed_events = sum(str(item.get("outcome") or "") != "succeeded" for item in events)
    requested = sorted(
        {
            name
            for item in events
            if str(item.get("outcome") or "") == "succeeded"
            for name in _requested_install_packages(str(item.get("command") or ""))
        }
    )
    added_names = {str(item.get("name") or "") for item in delta["added"]}
    return {
        "coverage": coverage,
        "install_events": events,
        "install_event_count": len(events),
        "failed_install_event_count": failed_events,
        "direct_requested_packages": requested,
        "direct_added_packages": sorted(added_names & set(requested)),
        "transitive_added_packages": sorted(added_names - set(requested)),
        "delta": delta,
    }


def _requested_install_packages(command: str) -> list[str]:
    match = re.search(r"(?:^|[;&|]\s*)(?:time\s+)?uv\s+pip\s+install\s+([^;&|]+)", command)
    if not match:
        return []
    try:
        words = shlex.split(match.group(1))
    except ValueError:
        return []
    packages: list[str] = []
    skip_value = False
    for word in words:
        if skip_value:
            skip_value = False
            continue
        if word in {"--python", "--index", "--default-index", "--cache-dir"}:
            skip_value = True
            continue
        if word.startswith("-") or word in {"2>&1", "1>/dev/null"}:
            continue
        try:
            packages.append(canonicalize_name(Requirement(word).name))
        except (InvalidRequirement, TypeError):
            continue
    return packages


def runner_token_summary(
    runner_meta: Mapping[str, Any],
    *,
    transcript_index: TranscriptIndex | None,
) -> tuple[str, dict[str, Any]]:
    usages: list[dict[str, Any]] = []
    lifecycle = runner_meta.get("session_lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    for invocation in lifecycle.get("invocations", []) if isinstance(lifecycle.get("invocations"), list) else []:
        if not isinstance(invocation, Mapping):
            continue
        provider = invocation.get("provider") if isinstance(invocation.get("provider"), Mapping) else {}
        usage = provider.get("usage") if isinstance(provider.get("usage"), Mapping) else None
        if usage:
            item = dict(usage)
            item.update(
                invocation_kind=str(invocation.get("kind") or ""),
                session_id=str(invocation.get("session_id") or ""),
            )
            usages.append(item)
    if usages:
        return "exact", {**aggregate_token_usage(usages), "invocations": usages}
    if transcript_index is not None:
        fallback = token_usage_from_transcript(transcript_index)
        if fallback is not None:
            return "partial", {**fallback, "invocations": [fallback]}
    return "unavailable", {**aggregate_token_usage([]), "invocations": []}


def build_attempt_observability(
    *,
    identity: Mapping[str, Any],
    status: str,
    runner_meta: Mapping[str, Any],
    transcript_index: TranscriptIndex | None,
    path_replacements: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if transcript_index is None:
        tools = {
            "coverage": "unavailable",
            "call_count": 0,
            "failure_count": 0,
            "failure_rate": 0.0,
            "status_counts": {},
            "by_name": {},
            "exec_call_count": 0,
            "exec_failure_count": 0,
            "exec_failure_rate": 0.0,
            "skill_runner_call_count": 0,
            "exec_calls": [],
        }
    else:
        tools = summarize_tool_events(
            transcript_index,
            path_replacements=path_replacements,
        )
    manifest = runner_meta.get("attempt_environment")
    packages = package_summary(manifest if isinstance(manifest, Mapping) else None)
    token_coverage, tokens = runner_token_summary(
        runner_meta,
        transcript_index=transcript_index,
    )
    resources = runner_meta.get("container_resources")
    resources = dict(resources) if isinstance(resources, Mapping) else {
        "coverage": "unavailable",
        "sample_count": 0,
    }
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "attempt_index": int(identity.get("attempt_index") or 0),
        "session_id": str(identity.get("session_id") or ""),
        "status": str(status or ""),
        "coverage": {
            "tools": tools.get("coverage", "unavailable"),
            "packages": packages.get("coverage", "unavailable"),
            "tokens": token_coverage,
            "timing": "exact",
            "resources": resources.get("coverage", "unavailable"),
        },
        "timing": {},
        "tokens": tokens,
        "tools": tools,
        "packages": packages,
        "resources": resources,
    }


def _coverage_for(attempts: list[dict[str, Any]], key: str) -> str:
    values = [str((item.get("coverage") or {}).get(key) or "unavailable") for item in attempts]
    if values and all(value == "exact" for value in values):
        return "exact"
    if any(value in {"exact", "partial"} for value in values):
        return "partial"
    return "unavailable"


def aggregate_attempt_observability(
    attempts: list[dict[str, Any]],
    *,
    record_wall_seconds: float | None = None,
    retry_backoff_seconds: float = 0.0,
    scoring_wait_seconds: float = 0.0,
    scoring_seconds: float = 0.0,
) -> dict[str, Any]:
    token_rows = [item.get("tokens") for item in attempts if isinstance(item.get("tokens"), Mapping)]
    token_totals = aggregate_token_usage([dict(item) for item in token_rows])
    token_totals["invocation_count"] = sum(int(item.get("invocation_count") or 0) for item in token_rows)
    tool_summaries = [item.get("tools") for item in attempts if isinstance(item.get("tools"), Mapping)]
    package_summaries = [item.get("packages") for item in attempts if isinstance(item.get("packages"), Mapping)]
    resource_summaries = [item.get("resources") for item in attempts if isinstance(item.get("resources"), Mapping)]
    tool_calls = sum(int(item.get("call_count") or 0) for item in tool_summaries)
    tool_failures = sum(int(item.get("failure_count") or 0) for item in tool_summaries)
    exec_calls = sum(int(item.get("exec_call_count") or 0) for item in tool_summaries)
    exec_failures = sum(int(item.get("exec_failure_count") or 0) for item in tool_summaries)
    statuses: Counter[str] = Counter()
    for item in tool_summaries:
        statuses.update({str(key): int(value) for key, value in (item.get("status_counts") or {}).items()})
    added_packages = sorted(
        {
            str(row.get("name") or "")
            for item in package_summaries
            for row in ((item.get("delta") or {}).get("added") or [])
            if isinstance(row, Mapping) and row.get("name")
        }
    )
    memory_peak = max((int(item.get("memory_peak_bytes") or 0) for item in resource_summaries), default=0)
    cpu_peak = max((float(item.get("cpu_peak_percent") or 0.0) for item in resource_summaries), default=0.0)
    pids_peak = max((int(item.get("pids_peak") or 0) for item in resource_summaries), default=0)
    attempt_seconds = sum(float((item.get("timing") or {}).get("attempt_wall_seconds") or 0.0) for item in attempts)
    agent_seconds = sum(float((item.get("timing") or {}).get("agent_seconds") or 0.0) for item in attempts)
    known_seconds = attempt_seconds + retry_backoff_seconds + scoring_wait_seconds + scoring_seconds
    wall = max(0.0, float(record_wall_seconds or 0.0))
    attempt_refs = [
        {
            "attempt_index": item.get("attempt_index"),
            "session_id": item.get("session_id"),
            "status": item.get("status"),
            "summary_path": item.get("summary_path"),
            "resources_path": item.get("resources_path"),
            "coverage": item.get("coverage") or {},
            "timing": item.get("timing") or {},
            "tokens": item.get("tokens") or {},
            "tools": {
                key: (item.get("tools") or {}).get(key)
                for key in ("call_count", "failure_count", "exec_call_count", "exec_failure_count", "status_counts")
            },
            "packages": {
                "install_event_count": (item.get("packages") or {}).get("install_event_count", 0),
                "failed_install_event_count": (item.get("packages") or {}).get("failed_install_event_count", 0),
                "delta": (item.get("packages") or {}).get("delta") or {},
            },
            "resources": item.get("resources") or {},
        }
        for item in attempts
    ]
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "coverage": {
            key: _coverage_for(attempts, key)
            for key in ("tools", "packages", "tokens", "timing", "resources")
        },
        "totals": {
            "timing": {
                "record_wall_seconds": wall,
                "attempt_wall_seconds": attempt_seconds,
                "agent_seconds": agent_seconds,
                "retry_backoff_seconds": retry_backoff_seconds,
                "scoring_wait_seconds": scoring_wait_seconds,
                "scoring_seconds": scoring_seconds,
                "overhead_seconds": max(0.0, wall - known_seconds),
            },
            "tokens": token_totals,
            "tools": {
                "call_count": tool_calls,
                "failure_count": tool_failures,
                "failure_rate": tool_failures / tool_calls if tool_calls else 0.0,
                "exec_call_count": exec_calls,
                "exec_failure_count": exec_failures,
                "exec_failure_rate": exec_failures / exec_calls if exec_calls else 0.0,
                "status_counts": dict(sorted(statuses.items())),
            },
            "packages": {
                "install_event_count": sum(int(item.get("install_event_count") or 0) for item in package_summaries),
                "failed_install_event_count": sum(int(item.get("failed_install_event_count") or 0) for item in package_summaries),
                "added_package_count": len(added_packages),
                "added_packages": added_packages,
            },
            "resources": {
                "attempts_sampled": sum(int(item.get("sample_count") or 0) > 0 for item in resource_summaries),
                "cpu_peak_percent": cpu_peak,
                "memory_peak_bytes": memory_peak,
                "pids_peak": pids_peak,
                "network_rx_bytes": sum(int(item.get("network_rx_bytes") or 0) for item in resource_summaries),
                "network_tx_bytes": sum(int(item.get("network_tx_bytes") or 0) for item in resource_summaries),
                "block_read_bytes": sum(int(item.get("block_read_bytes") or 0) for item in resource_summaries),
                "block_write_bytes": sum(int(item.get("block_write_bytes") or 0) for item in resource_summaries),
            },
        },
        "attempt_count": len(attempts),
        "attempts": attempt_refs,
        "final_attempt": attempt_refs[-1] if attempt_refs else None,
    }


def load_attempt_summaries(
    output_root: Path,
    *,
    group_id: str,
    record_id: str,
) -> list[dict[str, Any]]:
    root = (
        Path(output_root)
        / "observability"
        / "attempts"
        / _slug(group_id)
        / _slug(record_id)
    )
    if not root.is_dir() or root.is_symlink():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(root.glob("attempt-*/summary.json")):
        if path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            summaries.append(payload)
    return sorted(summaries, key=lambda item: int(item.get("attempt_index") or 0))


def unavailable_attempt_observability(
    identity: Mapping[str, Any],
    *,
    status: str,
    error: str,
) -> dict[str, Any]:
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "attempt_index": int(identity.get("attempt_index") or 0),
        "session_id": str(identity.get("session_id") or ""),
        "status": status,
        "coverage": {
            key: "unavailable"
            for key in ("tools", "packages", "tokens", "timing", "resources")
        },
        "timing": {},
        "tokens": {**aggregate_token_usage([]), "invocations": []},
        "tools": {},
        "packages": {},
        "resources": {},
        "collection_error": error,
    }


def finalize_record_observability(
    payload: Mapping[str, Any] | None,
    *,
    record_wall_seconds: float,
    scoring_wait_seconds: float = 0.0,
    scoring_seconds: float = 0.0,
) -> dict[str, Any]:
    current = dict(payload) if isinstance(payload, Mapping) else aggregate_attempt_observability([])
    coverage = dict(current.get("coverage") or {})
    coverage["timing"] = "exact"
    current["coverage"] = coverage
    totals = dict(current.get("totals") or {})
    timing = dict(totals.get("timing") or {})
    timing.update(
        record_wall_seconds=max(0.0, float(record_wall_seconds)),
        scoring_wait_seconds=max(0.0, float(scoring_wait_seconds)),
        scoring_seconds=max(0.0, float(scoring_seconds)),
    )
    known = sum(
        float(timing.get(key) or 0.0)
        for key in (
            "attempt_wall_seconds",
            "retry_backoff_seconds",
            "scoring_wait_seconds",
            "scoring_seconds",
        )
    )
    timing["overhead_seconds"] = max(0.0, timing["record_wall_seconds"] - known)
    totals["timing"] = timing
    current["totals"] = totals
    return current


def legacy_observability(result: Mapping[str, Any]) -> dict[str, Any]:
    runner_meta = result.get("runner_meta") if isinstance(result.get("runner_meta"), Mapping) else {}
    audit = runner_meta.get("skill_use_audit") if isinstance(runner_meta.get("skill_use_audit"), Mapping) else {}
    manifest = runner_meta.get("attempt_environment") if isinstance(runner_meta.get("attempt_environment"), Mapping) else {}
    agent_meta = runner_meta.get("agentMeta") if isinstance(runner_meta.get("agentMeta"), Mapping) else {}
    usage = agent_meta.get("usage") if isinstance(agent_meta.get("usage"), Mapping) else None
    tokens = _usage_payload(usage, source="legacy_agent_meta") if usage else aggregate_token_usage([])
    raw_elapsed = result.get("elapsed_seconds")
    elapsed_available = (
        isinstance(raw_elapsed, (int, float))
        and not isinstance(raw_elapsed, bool)
        and math.isfinite(float(raw_elapsed))
        and float(raw_elapsed) >= 0
    )
    elapsed = float(raw_elapsed) if elapsed_available else 0.0
    tool_calls = int(audit.get("openclaw_tool_call_count", audit.get("tool_call_count", 0)) or 0)
    tool_failures = max(
        int(audit.get("openclaw_tool_failure_count") or 0),
        int(audit.get("tool_failure_count") or 0),
        int(audit.get("tool_result_error_count") or 0),
    )
    packages = package_summary(manifest)
    retry = runner_meta.get("timeout_retry") if isinstance(runner_meta.get("timeout_retry"), Mapping) else {}
    attempt_count = int(retry.get("attempts") or (1 if runner_meta else 0))
    return {
        "schema_version": OBSERVABILITY_SCHEMA_VERSION,
        "legacy_projection": True,
        "coverage": {
            "timing": "exact" if elapsed_available else "unavailable",
            "tools": "partial" if audit else "unavailable",
            "packages": packages["coverage"],
            "tokens": "partial" if usage else "unavailable",
            "resources": "unavailable",
        },
        "totals": {
            "timing": {"record_wall_seconds": elapsed} if elapsed_available else {},
            "tokens": tokens,
            "tools": {
                "call_count": tool_calls,
                "failure_count": tool_failures,
                "failure_rate": tool_failures / tool_calls if tool_calls else 0.0,
                "exec_call_count": int(audit.get("exec_tool_call_count", 0) or 0),
                "exec_failure_count": int(audit.get("exec_tool_failure_count", 0) or 0),
            },
            "packages": {
                "install_event_count": packages.get("install_event_count", 0),
                "failed_install_event_count": packages.get("failed_install_event_count", 0),
                "added_package_count": len((packages.get("delta") or {}).get("added") or []),
                "added_packages": [row["name"] for row in (packages.get("delta") or {}).get("added", [])],
            },
            "resources": {},
        },
        "attempt_count": attempt_count,
        "attempts": [],
        "final_attempt": None,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows
