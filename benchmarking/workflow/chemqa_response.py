from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from benchmarking.scoring.evaluators import normalize_answer_tracks, normalize_space


def render_chemqa_submission_rationale(final_submission: dict[str, Any], *, final_answer_text: str = "") -> str:
    parts: list[str] = []
    summary = normalize_space(str(final_submission.get("summary") or ""))
    if summary:
        parts.extend(["Summary:", summary])

    submission_trace = list(final_submission.get("submission_trace") or [])
    if submission_trace:
        parts.append("")
        parts.append("Reasoning / submission trace:")
        for item in submission_trace:
            if not isinstance(item, dict):
                continue
            step = normalize_space(str(item.get("step") or item.get("phase") or "reasoning"))
            detail = normalize_space(str(item.get("detail") or item.get("summary") or item.get("finding") or ""))
            status = normalize_space(str(item.get("status") or ""))
            bullet = f"- {step}"
            if status:
                bullet += f" [{status}]"
            if detail:
                bullet += f": {detail}"
            parts.append(bullet)

    claim_anchors = list(final_submission.get("claim_anchors") or [])
    if claim_anchors:
        parts.append("")
        parts.append("Claim anchors:")
        for item in claim_anchors:
            if not isinstance(item, dict):
                continue
            claim = normalize_space(str(item.get("claim") or ""))
            anchor = normalize_space(str(item.get("anchor") or ""))
            if claim:
                parts.append(f"- {anchor + ': ' if anchor else ''}{claim}")

    evidence_limits = list(final_submission.get("evidence_limits") or [])
    if evidence_limits:
        parts.append("")
        parts.append("Evidence limits:")
        for item in evidence_limits:
            text = normalize_space(str(item or ""))
            if text:
                parts.append(f"- {text}")

    final_answer = normalize_space(final_answer_text or str(final_submission.get("direct_answer") or ""))
    if final_answer:
        parts.append("")
        parts.append(f"FINAL ANSWER: {final_answer}")

    return "\n".join(part for part in parts if part is not None).strip()


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}



def build_chemqa_response_from_submission(*, final_submission: dict[str, Any], final_answer_text: str = "") -> tuple[str, str]:
    short_answer_text = normalize_space(final_answer_text or str(final_submission.get("direct_answer") or ""))
    full_response_text = render_chemqa_submission_rationale(final_submission, final_answer_text=short_answer_text)
    return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=full_response_text)


def extract_chemqa_scoreable_answer(value: Any) -> str:
    if isinstance(value, str):
        stripped = normalize_space(value)
        if not stripped:
            return ""
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except Exception:
                return stripped
            return extract_chemqa_scoreable_answer(parsed)
        return stripped
    if isinstance(value, dict):
        for key in ("direct_answer", "answer", "value", "final_answer"):
            candidate = extract_chemqa_scoreable_answer(value.get(key))
            if candidate:
                return candidate
        return ""
    return ""


def build_chemqa_full_response(*, qa_result: dict[str, Any]) -> tuple[str, str]:
    artifact_paths = dict(qa_result.get("artifact_paths") or {})
    final_answer_artifact_path = str(artifact_paths.get("final_answer_artifact") or "").strip()
    if final_answer_artifact_path:
        path = Path(final_answer_artifact_path)
        if path.is_file():
            try:
                final_artifact = json.loads(path.read_text(encoding="utf-8"))
                short_answer_text = extract_chemqa_scoreable_answer(final_artifact.get("evaluator_answer"))
                full_response_text = str(final_artifact.get("full_answer") or final_artifact.get("display_answer") or "").strip()
                return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=full_response_text)
            except Exception:
                pass
    short_answer_text = extract_chemqa_scoreable_answer(qa_result.get("final_answer"))
    final_submission_path = str(artifact_paths.get("final_submission") or "").strip()
    if final_submission_path:
        path = Path(final_submission_path)
        if path.is_file():
            try:
                final_submission = json.loads(path.read_text(encoding="utf-8"))
                return build_chemqa_response_from_submission(final_submission=final_submission, final_answer_text=short_answer_text)
            except Exception:
                pass
    final_answer_path = str(artifact_paths.get("final_answer") or "").strip()
    if final_answer_path:
        path = Path(final_answer_path)
        if path.is_file():
            fallback_text = path.read_text(encoding="utf-8").strip()
            if not short_answer_text:
                return "", fallback_text
            return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text=fallback_text)
    if not short_answer_text:
        return "", ""
    return normalize_answer_tracks(short_answer_text=short_answer_text, full_response_text="")
