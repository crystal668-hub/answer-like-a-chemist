from __future__ import annotations

import re
from typing import Any, Protocol

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.skills.tree import render_top_level_skill_tree


FORMULA_SIGNAL_RE = re.compile(
    r"(?:\\\(|\\\[|[A-Za-z]_[A-Za-z0-9]+|\[[A-Za-z0-9_]+\]|\bK_[A-Za-z0-9]+|\bK_M\b|\^|/|=)"
)
NUMERIC_SCALAR_RE = re.compile(
    r"""
    ^\s*
    [-+]?
    (?:
        (?:
            (?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?
            |
            \.\d+
        )
        (?:
            \s*(?:[eE]|[xX]\s*10\^?)\s*[-+]?\d+
        )?
    )
    \s*$
    """,
    re.VERBOSE,
)


class RuntimeBundleLike(Protocol):
    bundle_dir: Any
    question_markdown: Any
    image_files: list[Any]


def _looks_like_formula_answer(record: BenchmarkRecord) -> bool:
    reference = str(getattr(record, "reference_answer", "") or "")
    prompt = str(getattr(record, "prompt", "") or "")
    if reference:
        if _reference_is_numeric_scalar(record):
            return False
        return bool(FORMULA_SIGNAL_RE.search(reference))
    return bool(FORMULA_SIGNAL_RE.search(prompt))


def _reference_is_numeric_scalar(record: BenchmarkRecord) -> bool:
    reference = str(getattr(record, "reference_answer", "") or "").strip()
    if not reference:
        return False
    if reference.startswith("$") and reference.endswith("$"):
        reference = reference[1:-1].strip()
    boxed_match = re.fullmatch(r"\\boxed\{([^{}]+)\}", reference)
    if boxed_match:
        reference = boxed_match.group(1).strip()
    return bool(NUMERIC_SCALAR_RE.fullmatch(reference))


def _hle_answer_type(record: BenchmarkRecord) -> str:
    payload = dict(getattr(record, "payload", {}) or {})
    config = dict(getattr(getattr(record, "grading", None), "config", {}) or {})
    raw_answer_type = str(payload.get("answer_type") or config.get("answer_type") or "").strip().lower()
    if "multiple" in raw_answer_type or "choice" in raw_answer_type:
        return "multiple_choice"
    if "exact" in raw_answer_type:
        return "exact_match"
    return "generic"


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
        if _hle_answer_type(record) == "multiple_choice":
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
        "Do not fabricate missing facts.",
        "Do not skip task-relevant derivation steps; include enough visible checks for grading.",
    ]
    if isinstance(time_budget_seconds, int) and time_budget_seconds > 0:
        instructions.extend(
            [
                f"Time budget: {time_budget_seconds} seconds for the whole answer attempt.",
                "Follow the Benchmark Coverage Checklist SOP in `act-like-a-chemist` to plan coverage, choose tools, and mark unresolved gaps.",
                "When coverage is sufficient or blocked, produce the requested final answer format immediately.",
            ]
        )
    if skills_enabled:
        instructions.append(render_top_level_skill_tree(available_skills=available_skills))
    else:
        instructions.append("Do not use OpenClaw skills or local skill tools for this run.")

    if record.eval_kind == "superchem_multiple_choice_rpf":
        instructions.append("This is a chemistry multiple-choice question.")
        instructions.append("Show visible option checks that distinguish the candidates, then end with exactly one line formatted as: FINAL ANSWER: <option letters>.")
        instructions.append("Cover checkpoint-like reasoning: key structure, mechanism, stoichiometry, and elimination checks that support or reject the candidate options.")
        instructions.append("If the gathered evidence is sufficient to distinguish the options, answer immediately instead of exploring more tools.")
        instructions.append("Provider skills may be used when they directly distinguish candidate options, verify a key structure or mechanism, or resolve material uncertainty.")
        instructions.append("Do not use provider skills to explore the skill tree, search for runners, or perform generic web/paper searches unrelated to the candidate options.")
        instructions.append("Use only uppercase option letters in the final answer; separate multiple correct letters with `|`.")
        if input_bundle is not None:
            instructions.append(f"Local file bundle: {input_bundle.bundle_dir}")
            instructions.append(f"Read the question bundle file first: {input_bundle.question_markdown}")
            if input_bundle.image_files:
                instructions.append("Inspect the local image files referenced in the bundle before answering.")
    elif record.eval_kind == "chembench_open_ended":
        if _reference_is_numeric_scalar(record):
            instructions.append("Show task-relevant formulas, substitutions, units, rounding, and checks, then end with exactly one line formatted as: FINAL ANSWER: <answer>.")
        else:
            instructions.append("Show minimal sufficient reasoning for the precise final string, structure, name, or count. Avoid adding irrelevant formulas, substitutions, or units.")
            instructions.append("End with exactly one line formatted as: FINAL ANSWER: <answer>.")
    elif record.eval_kind == "frontierscience_olympiad":
        instructions.append("End with exactly one line formatted as: FINAL ANSWER: <answer>.")
        instructions.append("The final line should contain only the requested value, expression, formula, structure name, or entity, including required units or rounding.")
        instructions.append("Do not provide multiple answer attempts.")
    elif record.eval_kind == "frontierscience_research":
        instructions.append("This is a FrontierScience research-track chemistry task scored against itemized reasoning criteria.")
        instructions.append("Provide a complete, structured response that covers every requested sub-question, condition, calculation, mechanism, protocol consequence, and conclusion.")
        instructions.append("Use numbered sections or clear headings when the prompt has multiple parts.")
        instructions.append("Include intermediate derivations, formulas, assumptions, units, and justifications needed for a rubric judge to award each point.")
        instructions.append("Do not collapse the response to a short final answer, and do not replace the detailed response with a concise summary.")
        instructions.append("If you include a conclusion, make it a supported conclusion section after the detailed reasoning, not a standalone short-answer line.")
    elif record.eval_kind == "hle":
        instructions.append("Use the official HLE response format exactly:")
        instructions.append("Explanation: <your visible derivation and checks>")
        instructions.append("Answer: <your chosen answer>")
        instructions.append("Confidence: <your confidence score between 0% and 100%>")
        if _hle_answer_type(record) == "multiple_choice":
            instructions.append("For HLE multiple-choice tasks, put only the option letter or letters in the `Answer:` field.")
        elif _hle_answer_type(record) == "exact_match":
            instructions.append("For HLE exact-match tasks, put only the final value, expression, or entity in the `Answer:` field.")
        instructions.append("Do not add `FINAL ANSWER:` to HLE responses.")
        if input_bundle is not None:
            instructions.append(f"Local file bundle: {input_bundle.bundle_dir}")
            instructions.append(f"Read the question bundle file first: {input_bundle.question_markdown}")
            if input_bundle.image_files:
                instructions.append("Inspect the local image files referenced in the bundle before answering.")
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
    elif record.eval_kind == "frontierscience_research":
        instructions.append("Provide a complete multi-part research answer that covers every requested condition, calculation, mechanism, protocol consequence, and conclusion.")
        instructions.append("Do not compress the response to a concise final answer; keep the rubric-relevant reasoning visible.")
    elif record.eval_kind == "hle":
        instructions.append("Use the official HLE response format exactly:")
        instructions.append("Explanation: <your visible derivation and checks>")
        instructions.append("Answer: <your chosen answer>")
        instructions.append("Confidence: <your confidence score between 0% and 100%>")
        if input_bundle is not None:
            instructions.append(f"Use the local file bundle at `{input_bundle.bundle_dir}`.")
            instructions.append(f"Open `{input_bundle.question_markdown}` first and inspect any referenced images.")
    instructions.append(f"ChemQA Artifact Flow answer kind: {resolve_chemqa_answer_kind(record)}.")
    return "\n".join(instructions) + "\n\nQUESTION:\n" + record.prompt.strip()
