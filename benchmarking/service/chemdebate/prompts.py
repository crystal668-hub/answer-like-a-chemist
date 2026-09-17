from __future__ import annotations

from benchmarking.core.prompt_inputs import RuntimeBundleLike
from benchmarking.core.records import BenchmarkRecord


def resolve_chemqa_answer_kind(record: BenchmarkRecord) -> str:
    if record.eval_kind != "verifier_grounded":
        raise ValueError("ChemQA accepts only verifier-grounded records.")
    return "verifier_grounded_candidate"


def build_chemqa_goal(
    record: BenchmarkRecord,
    *,
    websearch_enabled: bool,
    input_bundle: RuntimeBundleLike | None = None,
) -> str:
    answer_kind = resolve_chemqa_answer_kind(record)
    instructions = [
        "Solve the following chemistry benchmark question.",
        "Return a final answer that is faithful to the prompt.",
        f"ChemQA Artifact Flow answer kind: {answer_kind}.",
    ]
    if input_bundle is not None:
        instructions.append(f"Use the local file bundle at `{input_bundle.bundle_dir}`.")
        instructions.append(f"Open `{input_bundle.question_markdown}` first and inspect any referenced images.")
    return "\n".join(instructions) + "\n\nQUESTION:\n" + record.prompt.strip()
