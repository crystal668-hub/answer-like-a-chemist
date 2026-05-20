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
ANSWER_CORRECTNESS_METRICS = {
    "judge_accuracy",
    "semantic_match",
    "exact",
    "exact_str_match",
    "hle_judge_accuracy",
}
RUBRIC_SCORE_METRICS = {"rubric_points"}
SUPER_CHEM_RPF_EVAL_KIND = "superchem_multiple_choice_rpf"


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


def complete_text(text: Any) -> dict[str, Any]:
    value = str(text or "")
    return {"text": value, "truncated": False, "original_chars": len(value)}


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
                    "answer_text": complete_text(item.get("answer_text")),
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
        "你正在为 OpenClaw chemistry benchmark 执行自动化评估和经验提取。\n"
        "请读取 JSON input bundle，并产出严格匹配输出 schema 的 JSON report。\n"
        "默认使用中文撰写所有面向用户阅读的内容，包括摘要、模式、建议、逐题分析、证据说明和推荐项。\n"
        "report JSON 中所有模型生成的自然语言字符串值必须使用简体中文，尤其是 cross_record_patterns、"
        "architecture_recommendations、skill_orchestration_recommendations 和 per_record_analysis 内的分析、"
        "evidence、rationale、recommendation、details、comparison、summary 等字段值。\n"
        "保留 JSON 字段名、枚举值、record_id、group_id、metric 名称和文件路径原样，不要翻译结构化字段名。\n"
        "逐题分析需要比较实验组、最终答案、评分细节和轨迹证据。\n"
        "只能使用输入中提供的参考答案、评估器细节和轨迹摘要作为证据。\n"
        "不要修改文件。除非需要只读检查，否则不要运行项目命令。\n\n"
        f"Input bundle: {input_bundle_path}\n"
        f"Output schema: {report_schema_path}\n\n"
        "最终答案只返回 JSON。"
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
                "standard_answer_delta": "Codex 分析不可用；请查看 input-bundle.json 中的 reference_answer 和 evaluation 详情。",
                "trajectory_delta": "Codex 分析不可用；请查看 input-bundle.json 中的 trajectory 摘要。",
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


def markdown_table_cell(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("\n", " ").replace("\r", " ").replace("|", "\\|")


def format_number(value: Any, *, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    formatted = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return formatted or "0"


def format_percent(value: Any, *, digits: int = 1) -> str:
    try:
        number = float(value) * 100.0
    except (TypeError, ValueError):
        return ""
    formatted = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return f"{formatted or '0'}%"


def group_order_from_bundle(bundle: dict[str, Any]) -> list[str]:
    run_summary = bundle.get("run_summary") if isinstance(bundle, dict) else {}
    summary = (run_summary or {}).get("summary") or {}
    group_order = summary.get("group_order") if isinstance(summary, dict) else None
    if isinstance(group_order, list):
        ordered = [str(group_id) for group_id in group_order if str(group_id or "").strip()]
        if ordered:
            return ordered
    ordered: list[str] = []
    for record in bundle.get("records", []) if isinstance(bundle, dict) else []:
        if not isinstance(record, dict):
            continue
        for group in record.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            group_id = str(group.get("group_id") or "").strip()
            if group_id and group_id not in ordered:
                ordered.append(group_id)
    return ordered


def format_result_cell(group: dict[str, Any] | None) -> str:
    if not isinstance(group, dict):
        return "-"
    status_axes = group.get("status_axes") if isinstance(group.get("status_axes"), dict) else {}
    execution_error_kind = str(status_axes.get("execution_error_kind") or "").strip()
    if execution_error_kind:
        return f"执行错误: {execution_error_kind}"
    if status_axes.get("evaluable") is False:
        return "不可评估"
    if status_axes.get("scored") is False:
        return "未评分"

    evaluation = group.get("evaluation") if isinstance(group.get("evaluation"), dict) else {}
    if not evaluation:
        return "未评分"
    eval_kind = str(evaluation.get("eval_kind") or "").strip()
    primary_metric = str(evaluation.get("primary_metric") or "").strip()
    passed = bool(evaluation.get("passed"))

    if eval_kind == SUPER_CHEM_RPF_EVAL_KIND:
        details = evaluation.get("details") if isinstance(evaluation.get("details"), dict) else {}
        label = "答案正确" if passed else "错误"
        rpf = details.get("rpf")
        if rpf is None:
            return label
        return f"{label}; RPF {format_percent(rpf)}"
    if primary_metric in RUBRIC_SCORE_METRICS:
        score = format_number(evaluation.get("score"))
        max_score = format_number(evaluation.get("max_score"))
        normalized = format_percent(evaluation.get("normalized_score"))
        if score and max_score and normalized:
            return f"{score}/{max_score} ({normalized})"
        if score and max_score:
            return f"{score}/{max_score}"
    if primary_metric in ANSWER_CORRECTNESS_METRICS:
        return "正确" if passed else "错误"
    normalized = format_number(evaluation.get("normalized_score"))
    return normalized if normalized else ("正确" if passed else "错误")


def _group_entries(bundle: dict[str, Any], group_id: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for record in bundle.get("records", []) if isinstance(bundle, dict) else []:
        if not isinstance(record, dict):
            continue
        for group in record.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            if str(group.get("group_id") or "") == group_id:
                entries.append(group)
    return entries


def _scored_evaluation(group: dict[str, Any]) -> dict[str, Any] | None:
    status_axes = group.get("status_axes") if isinstance(group.get("status_axes"), dict) else {}
    if status_axes.get("evaluable") is False or status_axes.get("scored") is False:
        return None
    evaluation = group.get("evaluation") if isinstance(group.get("evaluation"), dict) else {}
    return evaluation if evaluation else None


def _average_values(values: list[float]) -> str:
    if not values:
        return ""
    return format_number(sum(values) / len(values))


def average_process_score(bundle: dict[str, Any], group_id: str) -> str:
    values: list[float] = []
    for group in _group_entries(bundle, group_id):
        evaluation = _scored_evaluation(group)
        if not evaluation:
            continue
        primary_metric = str(evaluation.get("primary_metric") or "").strip()
        if primary_metric not in RUBRIC_SCORE_METRICS:
            continue
        try:
            values.append(float(evaluation.get("normalized_score")))
        except (TypeError, ValueError):
            continue
    return _average_values(values)


def average_superchem_detail_metric(bundle: dict[str, Any], group_id: str, metric: str) -> str:
    values: list[float] = []
    for group in _group_entries(bundle, group_id):
        evaluation = _scored_evaluation(group)
        if not evaluation:
            continue
        if str(evaluation.get("eval_kind") or "").strip() != SUPER_CHEM_RPF_EVAL_KIND:
            continue
        details = evaluation.get("details") if isinstance(evaluation.get("details"), dict) else {}
        try:
            values.append(float(details.get(metric)))
        except (TypeError, ValueError):
            continue
    return _average_values(values)


def format_average_cell(bundle: dict[str, Any], group_id: str) -> str:
    run_summary = bundle.get("run_summary") if isinstance(bundle, dict) else {}
    summary_payload = (run_summary or {}).get("summary") or {}
    summary = summary_payload.get("groups") or {}
    group_summary = summary.get(group_id) if isinstance(summary, dict) else None
    if not isinstance(group_summary, dict):
        return "-"
    count = group_summary.get("count")
    pass_count = group_summary.get("pass_count")
    parts: list[str] = []
    try:
        count_number = int(count)
        pass_number = int(pass_count)
    except (TypeError, ValueError):
        count_number = 0
        pass_number = 0
    if count_number > 0:
        parts.append(f"正确率 {pass_number}/{count_number} ({format_percent(pass_number / count_number)})")
    avg_process_score = average_process_score(bundle, group_id)
    if avg_process_score:
        parts.append(f"平均分 {avg_process_score}")
    avg_answer_accuracy = average_superchem_detail_metric(bundle, group_id, "answer_accuracy")
    if avg_answer_accuracy:
        parts.append(f"答案均值 {avg_answer_accuracy}")
    avg_rpf = average_superchem_detail_metric(bundle, group_id, "rpf")
    if avg_rpf:
        parts.append(f"RPF 均值 {avg_rpf}")
    return "; ".join(parts) if parts else "-"


def render_per_record_result_table(bundle: dict[str, Any] | None) -> list[str]:
    if not isinstance(bundle, dict):
        return []
    records = [record for record in (bundle.get("records") or []) if isinstance(record, dict)]
    group_ids = group_order_from_bundle(bundle)
    if not records or not group_ids:
        return []
    header = ["题目", "评价方式", *group_ids]
    lines = ["## 每题结果表", ""]
    lines.append("| " + " | ".join(markdown_table_cell(cell) for cell in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for record in records:
        groups_by_id = {
            str(group.get("group_id") or ""): group
            for group in record.get("groups", []) or []
            if isinstance(group, dict)
        }
        row = [
            record.get("record_id") or "",
            record.get("eval_kind") or "",
            *(format_result_cell(groups_by_id.get(group_id)) for group_id in group_ids),
        ]
        lines.append("| " + " | ".join(markdown_table_cell(cell) for cell in row) + " |")
    average_row = ["平均", "-", *(format_average_cell(bundle, group_id) for group_id in group_ids)]
    lines.append("| " + " | ".join(markdown_table_cell(cell) for cell in average_row) + " |")
    lines.append("")
    return lines


def format_passed(value: Any) -> str:
    if value is True:
        return "通过"
    if value is False:
        return "未通过"
    return ""


def append_per_record_analysis_lines(lines: list[str], record: dict[str, Any]) -> None:
    reference_answer = record.get("reference_answer")
    if reference_answer not in (None, ""):
        lines.append(f"- 参考答案: {reference_answer}")
    summary = record.get("summary")
    if summary:
        lines.append(f"- 总结: {summary}")
    for key in ("standard_answer_delta", "trajectory_delta", "comparison"):
        value = record.get(key)
        if not value:
            continue
        labels = {
            "standard_answer_delta": "答案差异",
            "trajectory_delta": "轨迹差异",
            "comparison": "对比",
        }
        lines.append(f"- {labels[key]}: {value}")

    group_results = record.get("group_results") or []
    if isinstance(group_results, dict):
        group_results = [
            {"group_id": group_id, **payload}
            for group_id, payload in group_results.items()
            if isinstance(payload, dict)
        ]
    for group in group_results:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "<unknown>")
        details: list[str] = []
        final_answer = group.get("final_answer")
        if final_answer not in (None, ""):
            details.append(f"答案 {final_answer}")
        score = group.get("score")
        if score not in (None, ""):
            details.append(f"得分 {format_number(score)}")
        passed = format_passed(group.get("passed"))
        if passed:
            details.append(passed)
        suffix = f" {'；'.join(details)}" if details else ""
        lines.append(f"- {group_id}:{suffix}")
        evidence = group.get("evidence") or group.get("evaluator_summary")
        if evidence:
            lines.append(f"  - 证据: {evidence}")
        trajectory = group.get("trajectory_evidence")
        if trajectory:
            lines.append(f"  - 轨迹: {trajectory}")

    recommendations: list[Any] = []
    if record.get("recommendation"):
        recommendations.append(record.get("recommendation"))
    recs = record.get("recommendations") or []
    if isinstance(recs, list):
        recommendations.extend(recs)
    elif recs:
        recommendations.append(recs)
    for item in recommendations:
        lines.append(f"- 建议: {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")


def render_markdown_report(report: dict[str, Any], *, input_bundle: dict[str, Any] | None = None) -> str:
    lines = ["# 自动化 Benchmark 评估", ""]
    run_summary = report.get("run_summary") if isinstance(report.get("run_summary"), dict) else {}
    lines.append(f"- Schema 版本: {report.get('schema_version', '')}")
    if run_summary:
        lines.append(f"- 题目数: {run_summary.get('record_count', '')}")
        if run_summary.get("analysis_status"):
            lines.append(f"- 分析状态: {run_summary.get('analysis_status')}")
    lines.append("")
    table_lines = render_per_record_result_table(input_bundle)
    if table_lines:
        lines.extend(table_lines)
    lines.append("## 跨题模式")
    patterns = report.get("cross_record_patterns") or []
    if patterns:
        for item in patterns:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- 暂无跨题模式。")
    lines.append("")
    lines.append("## 架构建议")
    recommendations = report.get("architecture_recommendations") or []
    if recommendations:
        for item in recommendations:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- 暂无架构建议。")
    lines.append("")
    lines.append("## Skill 编排建议")
    skill_recommendations = report.get("skill_orchestration_recommendations") or []
    if skill_recommendations:
        for item in skill_recommendations:
            lines.append(f"- {item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}")
    else:
        lines.append("- 暂无 skill 编排建议。")
    lines.append("")
    lines.append("## 逐题分析")
    for record in report.get("per_record_analysis") or []:
        if not isinstance(record, dict):
            continue
        lines.append(f"### {record.get('record_id', '<unknown>')}")
        append_per_record_analysis_lines(lines, record)
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
    workspace_root = Path(__file__).resolve().parents[2]
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
    workspace_root = Path(__file__).resolve().parents[2]
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
        markdown_path.write_text(render_markdown_report(report, input_bundle=bundle), encoding="utf-8")
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
        markdown_path.write_text(render_markdown_report(report, input_bundle=bundle), encoding="utf-8")
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
    markdown_path.write_text(render_markdown_report(report, input_bundle=bundle), encoding="utf-8")
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
