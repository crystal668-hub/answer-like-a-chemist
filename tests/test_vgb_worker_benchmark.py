from argparse import Namespace
import json

from scripts import benchmark_vgb_worker


def test_requests_resolve_answers_and_repeat_only_marked_entries(tmp_path):
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"answer_text": "full historical answer"}), encoding="utf-8")
    source = tmp_path / "requests.jsonl"
    source.write_text(
        "\n".join([
            json.dumps({"track": "open_generation_xtb", "task_id": "x", "answer_result_path": str(result)}),
            json.dumps({"track": "open_generation_rdkit", "task_id": "a", "answer_text": "A", "repeatable": True}),
            json.dumps({"track": "property", "task_id": "b", "answer_text": "B", "repeatable": True}),
        ]) + "\n",
        encoding="utf-8",
    )
    args = Namespace(requests=str(source), cycle_requests_to=6, records=1)
    resolved = list(benchmark_vgb_worker.requests(args))
    assert [item["track"] for item in resolved] == ["open_generation_xtb", "open_generation_rdkit", "property", "open_generation_rdkit", "property", "open_generation_rdkit"]
    assert resolved[0]["answer_text"] == "full historical answer"
    assert all("repeatable" not in item and "answer_result_path" not in item for item in resolved)
