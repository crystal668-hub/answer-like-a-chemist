from __future__ import annotations

import re
from typing import Any, Protocol

from .datasets import BenchmarkRecord
from .skill_tree import render_top_level_skill_tree


FORMULA_SIGNAL_RE = re.compile(
    r"(?:\\\(|\\\[|[A-Za-z]_[A-Za-z0-9]+|\[[A-Za-z0-9_]+\]|\bK_[A-Za-z0-9]+|\bK_M\b|\^|/|=)"
)


class RuntimeBundleLike(Protocol):
    bundle_dir: Any
    question_markdown: Any
    image_files: list[Any]


def _looks_like_formula_answer(record: BenchmarkRecord) -> bool:
    reference = str(getattr(record, "reference_answer", "") or "")
    prompt = str(getattr(record, "prompt", "") or "")
    if reference:
        if re.fullmatch(r"\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*", reference):
            return False
        return bool(FORMULA_SIGNAL_RE.search(reference))
    return bool(FORMULA_SIGNAL_RE.search(prompt))


def resolve_chemqa_answer_kind(record: BenchmarkRecord) -> str:
    eval_kind = str(getattr(record, "eval_kind", "") or "").strip()
    dataset = str(getattr(record, "dataset", "") or "").strip()
    payload = dict(getattr(record, "payload", {}) or {})
    config = dict(getattr(getattr(record, "grading", None), "config", {}) or {})
    explicit = str(payload.get("answer_kind") or config.get("answer_kind") or "").strip()
    if explicit:
        return explicit
    if eval_kind == "frontierscience_olympiad" and _looks_like_formula_answer(record):
        return "formula_short_answer"
    if eval_kind in {"chembench_open_ended", "frontierscience_olympiad"}:
        return "numeric_short_answer"
    if eval_kind == "frontierscience_research" or str(config.get("track") or payload.get("track") or "").strip().lower() == "research":
        return "multi_part_research_answer"
    if eval_kind == "superchem_multiple_choice_rpf":
        return "multiple_choice"
    if dataset == "superchem" and isinstance(config.get("options") or payload.get("options"), dict):
        return "multiple_choice"
    if eval_kind == "hle":
        answer_type = str(payload.get("answer_type") or config.get("answer_type") or "").strip().lower()
        if "multiple" in answer_type or "choice" in answer_type:
            return "multiple_choice"
        return "generic_semantic_answer"
    return "generic_semantic_answer"


def build_single_llm_prompt(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    skills_enabled: bool = True,
    input_bundle: RuntimeBundleLike | None = None,
    available_skills: set[str] | None = None,
    time_budget_seconds: int | None = None,
) -> str:
    instructions = [
        "You are answering a chemistry benchmark question.",
        "Be careful, concise, and do not fabricate missing facts.",
    ]
    if isinstance(time_budget_seconds, int) and time_budget_seconds > 0:
        instructions.extend(
            [
                f"Time budget: {time_budget_seconds} seconds for the whole answer attempt.",
                "When roughly 20% or less of the budget remains, stop starting new tool or skill exploration.",
                "At that point, use the evidence already gathered and produce the requested final answer format immediately, even if uncertain.",
                "If a tool path fails twice or is unavailable, switch to best-effort chemistry reasoning instead of trying more variants of the same path.",
            ]
        )
    if skills_enabled:
        instructions.append(render_top_level_skill_tree(available_skills=available_skills))
    else:
        instructions.append("Do not use OpenClaw skills or local skill tools for this run.")

    if record.eval_kind == "superchem_multiple_choice_rpf":
        instructions.append("This is a chemistry multiple-choice question.")
        instructions.append("Show concise reasoning, then end with exactly one line formatted as: FINAL ANSWER: <option letters>.")
        instructions.append("If multiple options are correct, separate the letters with `|`.")
        if input_bundle is not None:
            instructions.append(f"Local file bundle: {input_bundle.bundle_dir}")
            instructions.append(f"Read the question bundle file first: {input_bundle.question_markdown}")
            if input_bundle.image_files:
                instructions.append("Inspect the local image files referenced in the bundle before answering.")
    elif record.eval_kind == "chembench_open_ended":
        instructions.append("Show brief reasoning if needed, then end with exactly one line formatted as: FINAL ANSWER: <answer>.")
    elif record.eval_kind == "frontierscience_olympiad":
        instructions.append("End with exactly one line formatted as: FINAL ANSWER: <answer>.")
    elif record.eval_kind == "hle":
        instructions.append("Use the official HLE response format exactly:")
        instructions.append("Explanation: <your concise explanation>")
        instructions.append("Answer: <your chosen answer>")
        instructions.append("Confidence: <your confidence score between 0% and 100%>")
    else:
        instructions.append("Provide a complete answer. If you include a final answer line, use: FINAL ANSWER: <answer>.")

    return "\n".join(instructions) + "\n\nQUESTION:\n" + record.prompt.strip()


def build_chemqa_goal(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    input_bundle: RuntimeBundleLike | None = None,
) -> str:
    instructions = [
        "Solve the following chemistry benchmark question.",
        "Return a final answer that is faithful to the prompt.",
    ]
    if record.eval_kind == "superchem_multiple_choice_rpf":
        instructions.append("This is a multiple-choice chemistry question.")
        instructions.append("End with a line `FINAL ANSWER: <option letters>`.")
        instructions.append("If multiple options are correct, separate the letters with `|`.")
        if input_bundle is not None:
            instructions.append(f"Use the local file bundle at `{input_bundle.bundle_dir}`.")
            instructions.append(f"Open `{input_bundle.question_markdown}` first and inspect any referenced images.")
    elif record.eval_kind in {"chembench_open_ended", "frontierscience_olympiad"}:
        instructions.append("If appropriate, end with a line `FINAL ANSWER: <answer>`.")
    elif record.eval_kind == "hle":
        instructions.append("Use the official HLE response format exactly:")
        instructions.append("Explanation: <your concise explanation>")
        instructions.append("Answer: <your chosen answer>")
        instructions.append("Confidence: <your confidence score between 0% and 100%>")
    instructions.append(f"ChemQA Artifact Flow answer kind: {resolve_chemqa_answer_kind(record)}.")
    return "\n".join(instructions) + "\n\nQUESTION:\n" + record.prompt.strip()
