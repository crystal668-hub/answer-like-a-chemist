from __future__ import annotations

import re
from benchmarking.core.prompt_inputs import RuntimeBundleLike, hle_answer_type

from benchmarking.core.datasets import BenchmarkRecord

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
        if hle_answer_type(record) == "multiple_choice":
            return "multiple_choice"
        return "generic_semantic_answer"
    if eval_kind == "verifier_grounded":
        return "verifier_grounded_candidate"
    return "generic_semantic_answer"




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
        instructions.append("Do not add the short-answer final marker used by non-research tasks to FrontierScience research responses.")
        instructions.append("End the response with this exact Markdown heading and section:")
        instructions.append("## FINAL RESEARCH ANSWER")
        instructions.append("<rubric-complete final synthesis>")
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
