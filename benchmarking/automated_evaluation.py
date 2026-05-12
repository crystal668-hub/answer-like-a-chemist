from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1
DEFAULT_CODEX_BIN = "codex"
CODEX_APP_BUNDLE_BIN = Path("/Applications/Codex.app/Contents/Resources/codex")
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_REASONING_EFFORT = "xhigh"
DEFAULT_CODEX_PREFLIGHT_TIMEOUT_SECONDS = 60
MAX_TEXT_CHARS = 6000
TEXT_ITEM_TYPES = {"text"}


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def truncate_text(text: Any, *, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    value = str(text or "")
    if len(value) <= max_chars:
        return {"text": value, "truncated": False, "original_chars": len(value)}
    return {
        "text": value[:max_chars],
        "truncated": True,
        "original_chars": len(value),
    }


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_from_content(content: Any) -> str:
    parts: list[str] = []
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") in TEXT_ITEM_TYPES:
            text = str(item.get("text") or "")
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _tool_calls_from_content(content: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(content, list):
        return calls
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "toolCall":
            continue
        calls.append(
            {
                "id": str(item.get("id") or item.get("toolCallId") or ""),
                "name": str(item.get("name") or item.get("toolName") or ""),
                "input_preview": truncate_text(json.dumps(item.get("input") or {}, ensure_ascii=False), max_chars=1200),
            }
        )
    return calls


def iter_transcript_messages(transcript_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return messages
    for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if isinstance(message, dict):
            messages.append(message)
    return messages


def summarize_single_llm_transcript(transcript_path: str | Path, *, max_text_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    path = Path(transcript_path).expanduser()
    summary: dict[str, Any] = {
        "kind": "single_llm_transcript",
        "path": str(path),
        "exists": path.is_file(),
        "sha256": "",
        "user_message_count": 0,
        "assistant_message_count": 0,
        "tool_result_count": 0,
        "user_prompt_preview": {},
        "assistant_text_tail": [],
        "tool_calls": [],
        "tool_results": [],
        "warnings": [],
    }
    if not path.is_file():
        summary["warnings"].append(f"missing transcript: {path}")
        return summary

    summary["sha256"] = file_hash(path)
    assistant_texts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for message in iter_transcript_messages(path):
        role = str(message.get("role") or "")
        content = message.get("content")
        if role == "user":
            summary["user_message_count"] += 1
            if not summary["user_prompt_preview"]:
                summary["user_prompt_preview"] = truncate_text(_text_from_content(content), max_chars=max_text_chars)
        elif role == "assistant":
            summary["assistant_message_count"] += 1
            text = _text_from_content(content)
            if text:
                assistant_texts.append(truncate_text(text, max_chars=max_text_chars))
            tool_calls.extend(_tool_calls_from_content(content))
        elif role == "toolResult":
            summary["tool_result_count"] += 1
            tool_results.append(
                {
                    "tool_call_id": str(message.get("toolCallId") or ""),
                    "tool_name": str(message.get("toolName") or ""),
                    "content_preview": truncate_text(_text_from_content(content), max_chars=max_text_chars),
                }
            )

    summary["assistant_text_tail"] = assistant_texts[-3:]
    summary["tool_calls"] = tool_calls[:50]
    summary["tool_results"] = tool_results[:50]
    summary["tool_call_count"] = len(tool_calls)
    return summary


def summarize_json_file(path: Path, *, max_text_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": str(path),
        "exists": path.is_file(),
        "sha256": "",
        "preview": None,
        "truncated": False,
        "error": "",
    }
    if not path.is_file():
        return payload
    payload["sha256"] = file_hash(path)
    try:
        loaded = load_json(path)
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
        payload["preview"] = truncate_text(path.read_text(encoding="utf-8", errors="replace"), max_chars=max_text_chars)
        payload["truncated"] = bool(payload["preview"].get("truncated"))
        return payload
    text = json.dumps(loaded, ensure_ascii=False)
    preview = truncate_text(text, max_chars=max_text_chars)
    payload["truncated"] = bool(preview["truncated"])
    if preview["truncated"]:
        payload["preview"] = preview
    else:
        payload["preview"] = loaded
    return payload


def summarize_chemqa_artifacts(runner_meta: dict[str, Any], *, max_text_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    archive_dir = Path(str(runner_meta.get("archive_dir") or "")).expanduser()
    qa_result_path_raw = str(runner_meta.get("qa_result_path") or "").strip()
    qa_result_path = Path(qa_result_path_raw).expanduser() if qa_result_path_raw else archive_dir / "qa_result.json"
    files = {
        "qa_result": qa_result_path,
        "artifact_manifest": archive_dir / "artifact_manifest.json",
        "candidate_view": archive_dir / "candidate_view.json",
        "final_answer_artifact": archive_dir / "final_answer_artifact.json",
        "failure_artifact": archive_dir / "failure_artifact.json",
        "proposer_trajectory": archive_dir / "proposer_trajectory.json",
        "reviewer_trajectories": archive_dir / "reviewer_trajectories.json",
        "review_statuses": archive_dir / "review_statuses.json",
        "validation_summary": archive_dir / "validation_summary.json",
    }
    summary = {
        "kind": "chemqa_artifacts",
        "archive_dir": str(archive_dir) if str(archive_dir) != "." else "",
        "archive_dir_exists": archive_dir.is_dir(),
        "files": {
            name: summarize_json_file(path, max_text_chars=max_text_chars)
            for name, path in files.items()
            if path != Path("")
        },
        "warnings": [],
    }
    if not archive_dir or str(archive_dir) == "." or not archive_dir.is_dir():
        summary["warnings"].append(f"missing ChemQA archive_dir: {archive_dir}")
    return summary


def group_results_by_record(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_record: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        by_record.setdefault(str(item.get("record_id") or ""), []).append(item)
    records: list[dict[str, Any]] = []
    for record_id in sorted(by_record):
        items = sorted(by_record[record_id], key=lambda value: str(value.get("group_id") or ""))
        first = items[0]
        records.append(
            {
                "record_id": record_id,
                "dataset": first.get("dataset"),
                "subset": first.get("subset"),
                "eval_kind": first.get("eval_kind"),
                "prompt_preview": truncate_text(first.get("prompt"), max_chars=2500),
                "reference_answer": truncate_text(first.get("reference_answer"), max_chars=5000),
                "groups": [],
            }
        )
        for item in items:
            runner_meta = item.get("runner_meta") if isinstance(item.get("runner_meta"), dict) else {}
            trajectory = summarize_result_trajectory(item, runner_meta=runner_meta)
            records[-1]["groups"].append(
                {
                    "group_id": item.get("group_id"),
                    "group_label": item.get("group_label"),
                    "runner": item.get("runner"),
                    "skills_enabled": item.get("skills_enabled"),
                    "websearch": item.get("websearch"),
                    "answer_text": truncate_text(item.get("answer_text"), max_chars=5000),
                    "short_answer_text": item.get("short_answer_text", ""),
                    "evaluation": item.get("evaluation") or {},
                    "status_axes": {
                        "run_lifecycle_status": item.get("run_lifecycle_status"),
                        "protocol_completion_status": item.get("protocol_completion_status"),
                        "answer_availability": item.get("answer_availability"),
                        "answer_reliability": item.get("answer_reliability"),
                        "evaluable": item.get("evaluable"),
                        "scored": item.get("scored"),
                        "recovery_mode": item.get("recovery_mode"),
                        "degraded_execution": item.get("degraded_execution"),
                        "execution_error_kind": item.get("execution_error_kind"),
                        "error": item.get("error"),
                    },
                    "skill_use_audit": runner_meta.get("skill_use_audit") or {},
                    "trajectory": trajectory,
                }
            )
    return records


def summarize_result_trajectory(item: dict[str, Any], *, runner_meta: dict[str, Any]) -> dict[str, Any]:
    runner = str(item.get("runner") or "")
    if runner == "single_llm":
        session = runner_meta.get("session_isolation") if isinstance(runner_meta.get("session_isolation"), dict) else {}
        transcript_path = str(session.get("postflight_entry_session_file") or "").strip()
        return summarize_single_llm_transcript(transcript_path) if transcript_path else {
            "kind": "single_llm_transcript",
            "exists": False,
            "path": "",
            "warnings": ["missing transcript path in runner_meta.session_isolation"],
        }
    if runner == "chemqa":
        return summarize_chemqa_artifacts(runner_meta)
    return {"kind": "unknown", "runner": runner, "warnings": [f"unsupported runner: {runner}"]}


def build_input_bundle(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    results_path = root / "results.json"
    manifest_path = root / "runtime-manifest.json"
    results_payload = load_json(results_path)
    runtime_manifest = load_json(manifest_path) if manifest_path.is_file() else {}
    raw_results = results_payload.get("results") if isinstance(results_payload, dict) else []
    results = [item for item in raw_results if isinstance(item, dict)]
    records = group_results_by_record(results)
    warnings: list[dict[str, Any]] = []
    for record in records:
        for group in record.get("groups", []):
            trajectory = group.get("trajectory") if isinstance(group, dict) else {}
            for warning in trajectory.get("warnings", []) if isinstance(trajectory, dict) else []:
                warnings.append(
                    {
                        "record_id": record.get("record_id"),
                        "group_id": group.get("group_id"),
                        "message": str(warning),
                    }
                )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "output_root": str(root),
        "source_files": {
            "results_json": str(results_path),
            "runtime_manifest": str(manifest_path),
        },
        "run_summary": {
            "generated_at": results_payload.get("generated_at"),
            "record_count": len(records),
            "group_count": len(results_payload.get("groups") or []),
            "result_count": len(results),
            "summary": results_payload.get("summary") or {},
            "runtime_manifest": runtime_manifest,
        },
        "records": records,
        "warnings": warnings,
    }


def automated_evaluation_prompt(input_bundle_path: Path, report_schema_path: Path) -> str:
    return (
        "You are performing automated benchmark evaluation and experience extraction for OpenClaw chemistry benchmarks.\n"
        "Read the JSON input bundle and produce a strict JSON report matching the provided schema.\n"
        "Analyze every record and compare experiment groups, final answers, scoring details, and trajectory evidence.\n"
        "Use only the supplied reference answers, evaluator details, and trajectory summaries as evidence.\n"
        "Do not modify files. Do not run project commands except read-only inspection if necessary.\n\n"
        f"Input bundle: {input_bundle_path}\n"
        f"Output schema: {report_schema_path}\n\n"
        "Return only JSON for the final answer."
    )


def report_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": True,
        "required": [
            "schema_version",
            "run_summary",
            "per_record_analysis",
            "cross_record_patterns",
            "architecture_recommendations",
            "skill_orchestration_recommendations",
        ],
        "properties": {
            "schema_version": {"type": "integer"},
            "run_summary": {"type": "object"},
            "per_record_analysis": {"type": "array"},
            "cross_record_patterns": {"type": "array"},
            "architecture_recommendations": {"type": "array"},
            "skill_orchestration_recommendations": {"type": "array"},
        },
    }


def fallback_report(bundle: dict[str, Any], *, reason: str) -> dict[str, Any]:
    per_record = []
    for record in bundle.get("records", []):
        groups = record.get("groups") if isinstance(record, dict) else []
        per_record.append(
            {
                "record_id": record.get("record_id"),
                "group_matrix": [
                    {
                        "group_id": group.get("group_id"),
                        "runner": group.get("runner"),
                        "passed": ((group.get("evaluation") or {}).get("passed") if isinstance(group, dict) else None),
                        "normalized_score": ((group.get("evaluation") or {}).get("normalized_score") if isinstance(group, dict) else None),
                        "answer_reliability": ((group.get("status_axes") or {}).get("answer_reliability") if isinstance(group, dict) else None),
                    }
                    for group in groups
                    if isinstance(group, dict)
                ],
                "standard_answer_delta": "Codex analysis unavailable; inspect reference_answer and evaluation details in input-bundle.json.",
                "trajectory_delta": "Codex analysis unavailable; inspect trajectory summaries in input-bundle.json.",
                "recommendations": [],
                "evidence_refs": [],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_summary": {
            "record_count": (bundle.get("run_summary") or {}).get("record_count"),
            "analysis_status": "fallback",
            "reason": reason,
        },
        "per_record_analysis": per_record,
        "cross_record_patterns": [],
        "architecture_recommendations": [],
        "skill_orchestration_recommendations": [],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    lines = ["# Automated Benchmark Evaluation", ""]
    run_summary = report.get("run_summary") if isinstance(report.get("run_summary"), dict) else {}
    lines.append(f"- Schema version: {report.get('schema_version', '')}")
    if run_summary:
        lines.append(f"- Records: {run_summary.get('record_count', '')}")
        if run_summary.get("analysis_status"):
            lines.append(f"- Analysis status: {run_summary.get('analysis_status')}")
    lines.append("")
    lines.append("## Cross-Record Patterns")
    patterns = report.get("cross_record_patterns") or []
    if patterns:
        for item in patterns:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- No cross-record patterns reported.")
    lines.append("")
    lines.append("## Architecture Recommendations")
    recommendations = report.get("architecture_recommendations") or []
    if recommendations:
        for item in recommendations:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- No architecture recommendations reported.")
    lines.append("")
    lines.append("## Skill Orchestration Recommendations")
    skill_recommendations = report.get("skill_orchestration_recommendations") or []
    if skill_recommendations:
        for item in skill_recommendations:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- No skill orchestration recommendations reported.")
    lines.append("")
    lines.append("## Per-Record Analysis")
    for record in report.get("per_record_analysis") or []:
        if not isinstance(record, dict):
            continue
        lines.append(f"### {record.get('record_id', '<unknown>')}")
        for key in ("standard_answer_delta", "trajectory_delta"):
            value = record.get(key)
            if value:
                lines.append(f"- {key}: {value}")
        recs = record.get("recommendations") or []
        for item in recs:
            lines.append(f"- recommendation: {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_report(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    required = set(report_schema()["required"])
    return required.issubset(payload.keys())


def extract_last_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _codex_config_args() -> list[str]:
    return [
        "--model",
        DEFAULT_CODEX_MODEL,
        "-c",
        f'model_reasoning_effort="{DEFAULT_CODEX_REASONING_EFFORT}"',
    ]


def _candidate_status(source: str, path: str | Path | None) -> dict[str, Any]:
    raw_path = str(path or "").strip()
    payload: dict[str, Any] = {
        "source": source,
        "path": raw_path,
        "exists": False,
        "executable": False,
        "usable": False,
    }
    if not raw_path:
        return payload
    resolved_path = Path(raw_path).expanduser()
    try:
        resolved_path = resolved_path.resolve()
    except OSError:
        pass
    payload["path"] = str(resolved_path)
    payload["exists"] = resolved_path.is_file()
    payload["executable"] = os.access(resolved_path, os.X_OK)
    payload["usable"] = bool(payload["exists"] and payload["executable"])
    return payload


def resolve_codex_binary(
    codex_bin: str | Path | None = None,
    *,
    which_func: Callable[[str], str | None] = shutil.which,
    app_bundle_bin: str | Path = CODEX_APP_BUNDLE_BIN,
) -> tuple[str | None, list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    explicit = str(codex_bin or "").strip()
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.is_absolute() or explicit_path.parent != Path("."):
            candidates.append(_candidate_status("explicit", explicit_path))
        else:
            resolved_explicit = which_func(explicit)
            candidates.append(_candidate_status("explicit", resolved_explicit or explicit))

    path_candidate = which_func(DEFAULT_CODEX_BIN)
    candidates.append(_candidate_status("path", path_candidate))
    candidates.append(_candidate_status("app_bundle", app_bundle_bin))
    for candidate in candidates:
        if candidate.get("usable"):
            return str(candidate["path"]), candidates
    return None, candidates


def codex_exec_command(
    *,
    codex_bin: str,
    workspace_root: Path,
    last_message_path: Path,
    prompt: str,
) -> list[str]:
    return [
        codex_bin,
        "--ask-for-approval",
        "never",
        "exec",
        "--cd",
        str(workspace_root),
        "--sandbox",
        "read-only",
        "--json",
        *_codex_config_args(),
        "--output-last-message",
        str(last_message_path),
        prompt,
    ]


def run_codex_preflight(
    *,
    analysis_dir: Path,
    timeout_seconds: int = DEFAULT_CODEX_PREFLIGHT_TIMEOUT_SECONDS,
    codex_bin: str = DEFAULT_CODEX_BIN,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    workspace_root = Path(__file__).resolve().parents[1]
    last_message_path = analysis_dir / "codex-preflight-last-message.txt"
    events_path = analysis_dir / "codex-preflight-events.jsonl"
    command = codex_exec_command(
        codex_bin=codex_bin,
        workspace_root=workspace_root,
        last_message_path=last_message_path,
        prompt="hello",
    )
    started = time.time()
    try:
        result = run_subprocess(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(workspace_root),
        )
    except Exception as exc:
        return {
            "status": "failed",
            "stage": "codex_preflight",
            "error": f"Codex preflight failed: {type(exc).__name__}: {exc}",
            "command": command,
            "elapsed_seconds": time.time() - started,
        }
    events_path.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        return {
            "status": "failed",
            "stage": "codex_preflight",
            "error": f"Codex preflight failed with return code {result.returncode}.",
            "returncode": result.returncode,
            "stdout_path": str(events_path),
            "stderr": result.stderr,
            "last_message_path": str(last_message_path),
            "command": command,
            "elapsed_seconds": time.time() - started,
        }
    return {
        "status": "completed",
        "stage": "codex_preflight",
        "stdout_path": str(events_path),
        "last_message_path": str(last_message_path),
        "command": command,
        "elapsed_seconds": time.time() - started,
    }


def run_codex_analysis(
    *,
    output_root: Path,
    analysis_dir: Path,
    input_bundle_path: Path,
    timeout_seconds: int,
    codex_bin: str = DEFAULT_CODEX_BIN,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    schema_path = analysis_dir / "report-schema.json"
    last_message_path = analysis_dir / "codex-last-message.txt"
    events_path = analysis_dir / "codex-events.jsonl"
    save_json(schema_path, report_schema())
    prompt = automated_evaluation_prompt(input_bundle_path, schema_path)
    workspace_root = Path(__file__).resolve().parents[1]
    command = codex_exec_command(
        codex_bin=codex_bin,
        workspace_root=workspace_root,
        last_message_path=last_message_path,
        prompt=prompt,
    )
    started = time.time()
    try:
        result = run_subprocess(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
            cwd=str(workspace_root),
        )
    except Exception as exc:
        return None, {
            "status": "failed",
            "stage": "codex_exec",
            "error": f"{type(exc).__name__}: {exc}",
            "command": command,
            "elapsed_seconds": time.time() - started,
        }
    events_path.write_text(result.stdout or "", encoding="utf-8")
    last_message = last_message_path.read_text(encoding="utf-8") if last_message_path.is_file() else ""
    parsed = extract_last_json_object(last_message)
    if result.returncode != 0:
        return None, {
            "status": "failed",
            "stage": "codex_exec",
            "returncode": result.returncode,
            "stdout_path": str(events_path),
            "stderr": result.stderr,
            "last_message_path": str(last_message_path),
            "command": command,
            "elapsed_seconds": time.time() - started,
        }
    if parsed is None or not validate_report(parsed):
        return None, {
            "status": "failed",
            "stage": "report_validation",
            "error": "Codex final message did not contain a schema-valid report JSON object.",
            "stdout_path": str(events_path),
            "stderr": result.stderr,
            "last_message_path": str(last_message_path),
            "command": command,
            "elapsed_seconds": time.time() - started,
        }
    return parsed, {
        "status": "completed",
        "stage": "completed",
        "stdout_path": str(events_path),
        "last_message_path": str(last_message_path),
        "command": command,
        "elapsed_seconds": time.time() - started,
    }


def run_automated_evaluation(
    output_root: str | Path,
    *,
    timeout_seconds: int = 3600,
    codex_bin: str | None = None,
    codex_which: Callable[[str], str | None] = shutil.which,
    app_bundle_bin: str | Path = CODEX_APP_BUNDLE_BIN,
    run_subprocess: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    analysis_dir = root / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    status_path = analysis_dir / "status.json"
    save_json(
        status_path,
        {
            "status": "running",
            "started_at": utc_timestamp(),
            "output_root": str(root),
        },
    )
    bundle = build_input_bundle(root)
    input_bundle_path = analysis_dir / "input-bundle.json"
    save_json(input_bundle_path, bundle)
    resolved_codex, codex_candidates = resolve_codex_binary(
        codex_bin,
        which_func=codex_which,
        app_bundle_bin=app_bundle_bin,
    )
    if not resolved_codex:
        codex_status = {
            "status": "failed",
            "stage": "codex_resolve",
            "error": "No executable Codex binary found.",
            "codex_candidates": codex_candidates,
        }
        report = fallback_report(bundle, reason=str(codex_status["error"]))
        report_path = analysis_dir / "report.json"
        markdown_path = analysis_dir / "report.md"
        save_json(report_path, report)
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        final_status = {
            **codex_status,
            "updated_at": utc_timestamp(),
            "output_root": str(root),
            "input_bundle_path": str(input_bundle_path),
            "report_path": str(report_path),
            "markdown_report_path": str(markdown_path),
        }
        save_json(status_path, final_status)
        return final_status
    preflight_status = run_codex_preflight(
        analysis_dir=analysis_dir,
        codex_bin=resolved_codex,
        run_subprocess=run_subprocess,
    )
    if preflight_status.get("status") != "completed":
        report = fallback_report(bundle, reason=str(preflight_status.get("error") or preflight_status.get("stage")))
        report_path = analysis_dir / "report.json"
        markdown_path = analysis_dir / "report.md"
        save_json(report_path, report)
        markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
        final_status = {
            **preflight_status,
            "codex_candidates": codex_candidates,
            "updated_at": utc_timestamp(),
            "output_root": str(root),
            "input_bundle_path": str(input_bundle_path),
            "report_path": str(report_path),
            "markdown_report_path": str(markdown_path),
        }
        save_json(status_path, final_status)
        return final_status
    report, codex_status = run_codex_analysis(
        output_root=root,
        analysis_dir=analysis_dir,
        input_bundle_path=input_bundle_path,
        timeout_seconds=timeout_seconds,
        codex_bin=resolved_codex,
        run_subprocess=run_subprocess,
    )
    if report is None:
        report = fallback_report(bundle, reason=str(codex_status.get("error") or codex_status.get("stage") or "codex_failed"))
    report_path = analysis_dir / "report.json"
    markdown_path = analysis_dir / "report.md"
    save_json(report_path, report)
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    final_status = {
        **codex_status,
        "codex_candidates": codex_candidates,
        "preflight": preflight_status,
        "updated_at": utc_timestamp(),
        "output_root": str(root),
        "input_bundle_path": str(input_bundle_path),
        "report_path": str(report_path),
        "markdown_report_path": str(markdown_path),
    }
    save_json(status_path, final_status)
    return final_status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run automated post-benchmark evaluation.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--output-root", required=True)
    run_parser.add_argument("--timeout-seconds", type=int, default=3600)
    run_parser.add_argument("--codex-bin", default=os.environ.get("BENCHMARK_AUTOMATED_EVALUATION_CODEX_BIN", ""))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        status = run_automated_evaluation(
            args.output_root,
            timeout_seconds=args.timeout_seconds,
            codex_bin=args.codex_bin or None,
        )
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if status.get("status") == "completed" else 1
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
