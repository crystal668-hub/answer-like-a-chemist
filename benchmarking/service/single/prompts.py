from __future__ import annotations
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.core.prompt_inputs import RuntimeBundleLike, hle_answer_type
from benchmarking.skills.tree import render_top_level_skill_tree

def build_single_llm_prompt(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    skills_enabled: bool = True,
    input_bundle: RuntimeBundleLike | None = None,
    configured_skills: set[str] | None = None,
    time_budget_seconds: int | None = None,
) -> str:
    instructions: list[str] = []
    if isinstance(time_budget_seconds, int) and time_budget_seconds > 0:
        instructions.append(f"Time budget: {time_budget_seconds} seconds for the whole answer attempt.")
    if skills_enabled:
        instructions.append(render_top_level_skill_tree(configured_skills=configured_skills))

    if record.eval_kind == "superchem_multiple_choice_rpf":
        instructions.append("End with exactly one line formatted as: FINAL ANSWER: <option letters>.")
        instructions.append("Use only uppercase option letters in the final answer; separate multiple correct letters with `|`.")
        if input_bundle is not None:
            instructions.append(f"Local file bundle: {input_bundle.bundle_dir}")
            instructions.append(f"Read the question bundle file first: {input_bundle.question_markdown}")
            if input_bundle.image_files:
                instructions.append("Inspect the local image files referenced in the bundle before answering.")
    elif record.eval_kind == "chembench_open_ended":
        instructions.append("End with exactly one line formatted as: FINAL ANSWER: <answer>.")
    elif record.eval_kind == "frontierscience_olympiad":
        pass
    elif record.eval_kind == "frontierscience_research":
        instructions.append("Do not add the short-answer final marker used by non-research tasks to FrontierScience research responses.")
        instructions.append("End the response with this exact Markdown heading followed by the final synthesis:")
        instructions.append("## FINAL RESEARCH ANSWER")
    elif record.eval_kind == "hle":
        instructions.append("Use the official HLE response format exactly:")
        instructions.append("Explanation: <your visible derivation and checks>")
        instructions.append("Answer: <your chosen answer>")
        instructions.append("Confidence: <your confidence score between 0% and 100%>")
        if hle_answer_type(record) == "multiple_choice":
            instructions.append("For HLE multiple-choice tasks, put only the option letter or letters in the `Answer:` field.")
        elif hle_answer_type(record) == "exact_match":
            instructions.append("For HLE exact-match tasks, put only the final value, expression, or entity in the `Answer:` field.")
        instructions.append("Do not add `FINAL ANSWER:` to HLE responses.")
        if input_bundle is not None:
            instructions.append(f"Local file bundle: {input_bundle.bundle_dir}")
            instructions.append(f"Read the question bundle file first: {input_bundle.question_markdown}")
            if input_bundle.image_files:
                instructions.append("Inspect the local image files referenced in the bundle before answering.")

    prefix = "\n".join(instructions)
    question = getattr(input_bundle, "prompt_text", None) or record.prompt
    return (prefix + "\n\n" if prefix else "") + question.strip()
