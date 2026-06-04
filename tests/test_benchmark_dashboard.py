from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarking.dashboard import annotations as dashboard_annotations
from benchmarking.dashboard import service as dashboard_service


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def result_payload(
    *,
    group_id: str,
    record_id: str,
    eval_kind: str = "chembench_open_ended",
    passed: bool | None = True,
    score: float = 1.0,
    primary_metric: str = "judge_accuracy",
    details: dict[str, object] | None = None,
    runner_meta: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "group_id": group_id,
        "group_label": group_id,
        "runner": "single_llm",
        "websearch": False,
        "skills_enabled": group_id.endswith("skills_on"),
        "record_id": record_id,
        "subset": "chembench",
        "dataset": "demo",
        "source_file": "/tmp/demo.jsonl",
        "eval_kind": eval_kind,
        "prompt": "Question body",
        "reference_answer": "Reference answer",
        "answer_text": f"FINAL ANSWER: {group_id}",
        "short_answer_text": group_id,
        "full_response_text": f"FINAL ANSWER: {group_id}",
        "evaluation": {
            "eval_kind": eval_kind,
            "score": score,
            "max_score": 1.0,
            "normalized_score": score,
            "passed": passed,
            "primary_metric": primary_metric,
            "primary_metric_direction": "higher_is_better",
            "details": details or {},
        },
        "runner_meta": runner_meta or {},
        "raw": {},
        "elapsed_seconds": 12.5,
        "run_lifecycle_status": "completed",
        "protocol_completion_status": "missing",
        "protocol_acceptance_status": None,
        "answer_availability": "native_final",
        "answer_reliability": "native",
        "evaluable": True,
        "scored": True,
        "recovery_mode": "none",
        "degraded_execution": False,
        "execution_error_kind": None,
        "error": None,
    }


def write_demo_run(root: Path, run_id: str = "benchmark-20260604-120000") -> Path:
    run_root = root / run_id
    result = result_payload(group_id="single_llm_skills_on", record_id="r1")
    write_json(
        run_root / "results.json",
        {
            "schema_version": 2,
            "generated_at": "2026-06-04T12:00:00+0800",
            "records": 1,
            "groups": [
                {
                    "id": "single_llm_skills_on",
                    "label": "skills on",
                    "runner": "single_llm",
                    "websearch": False,
                    "skills_enabled": True,
                }
            ],
            "results": [result],
            "summary": {
                "group_order": ["single_llm_skills_on"],
                "groups": {
                    "single_llm_skills_on": {
                        "count": 1,
                        "pass_count": 1,
                        "avg_normalized_score": 1.0,
                    }
                },
            },
        },
    )
    write_json(run_root / "runtime-manifest.json", {"run_groups": ["single_llm_skills_on"]})
    write_json(run_root / "per-record" / "single_llm_skills_on" / "r1.json", result)
    return run_root


def test_list_runs_reads_schema_v2_results_and_annotations(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    store = dashboard_annotations.AnnotationStore(tmp_path / "dashboard.sqlite")
    store.upsert_run_metadata(run_id=run_root.name, alias="Verifier smoke", favorite=True)

    dashboard = dashboard_service.BenchmarkDashboard(run_roots=[tmp_path], annotation_store=store)

    runs = dashboard.list_runs()

    assert [item["run_id"] for item in runs] == [run_root.name]
    assert runs[0]["alias"] == "Verifier smoke"
    assert runs[0]["favorite"] is True
    assert runs[0]["status"] == "completed"
    assert runs[0]["record_count"] == 1
    assert runs[0]["group_count"] == 1
    assert runs[0]["datasets"] == ["demo"]
    assert runs[0]["subsets"] == ["chembench"]
    assert runs[0]["average_normalized_score"] == 1.0
    assert runs[0]["progress"]["completed"] == 1
    assert runs[0]["summary"]["groups"]["single_llm_skills_on"]["avg_normalized_score"] == 1.0


def test_get_record_preserves_verifier_score_without_marking_failed(tmp_path: Path) -> None:
    verifier_details = {
        "status": "ok",
        "canonical_smiles": "CCO",
        "properties": {"qed": 0.92},
        "constraint_scores": [{"property": "qed", "score": 0.92}],
    }
    run_root = tmp_path / "verifier-run"
    payload = result_payload(
        group_id="single_llm_skills_on",
        record_id="rdkit_qed_max_001",
        eval_kind="verifier_grounded",
        passed=None,
        score=0.92,
        primary_metric="verifier_score",
        details=verifier_details,
    )
    write_json(
        run_root / "results.json",
        {
            "schema_version": 2,
            "generated_at": "2026-06-04T12:00:00+0800",
            "records": 1,
            "groups": [{"id": "single_llm_skills_on"}],
            "results": [payload],
            "summary": {},
        },
    )
    write_json(run_root / "per-record" / "single_llm_skills_on" / "rdkit-qed-max-001.json", payload)
    dashboard = dashboard_service.BenchmarkDashboard(run_roots=[tmp_path])

    record = dashboard.get_record("verifier-run", "rdkit_qed_max_001")

    group = record["groups"][0]
    assert group["score_label"] == "Verifier 0.92"
    assert group["outcome"] == "scored"
    assert group["evaluation"]["passed"] is None
    assert group["verifier"]["canonical_smiles"] == "CCO"
    assert group["verifier"]["properties"]["qed"] == 0.92


def test_get_record_uses_runtime_bundle_question_markdown_and_assets(tmp_path: Path) -> None:
    run_root = tmp_path / "superchem-run"
    bundle_root = run_root / "input-bundles" / "superchem-r1"
    question_path = bundle_root / "question.md"
    image_path = bundle_root / "images" / "img01.png"
    question_path.parent.mkdir(parents=True)
    question_path.write_text("Question with image ![](images/img01.png)", encoding="utf-8")
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"png")
    payload = result_payload(
        group_id="single_llm_skills_on",
        record_id="superchem-r1",
        eval_kind="superchem_multiple_choice_rpf",
        runner_meta={
            "runtime_bundle": {
                "question_markdown": str(question_path),
                "image_files": [str(image_path)],
            }
        },
    )
    write_json(run_root / "per-record" / "single_llm_skills_on" / "superchem-r1.json", payload)
    dashboard = dashboard_service.BenchmarkDashboard(run_roots=[tmp_path])

    record = dashboard.get_record("superchem-run", "superchem-r1")

    assert record["question_markdown"] == "Question with image ![](images/img01.png)"
    assert record["assets"][0]["relative_path"] == "input-bundles/superchem-r1/images/img01.png"
    assert dashboard.resolve_asset("superchem-run", "input-bundles/superchem-r1/images/img01.png") == image_path.resolve()
    with pytest.raises(dashboard_service.AssetAccessError):
        dashboard.resolve_asset("superchem-run", "../outside.txt")


def test_annotation_crud_is_soft_delete_and_does_not_touch_results(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    results_before = (run_root / "results.json").read_text(encoding="utf-8")
    store = dashboard_annotations.AnnotationStore(tmp_path / "dashboard.sqlite")

    created = store.create_annotation(
        run_id=run_root.name,
        record_id="r1",
        group_id="single_llm_skills_on",
        note="needs review",
        status="needs_review",
        tags=["numeric", "priority"],
        manual_verdict="uncertain",
    )
    updated = store.update_annotation(created["id"], note="checked", tags=["done"])
    store.delete_annotation(created["id"])

    assert updated["note"] == "checked"
    assert updated["tags"] == ["done"]
    assert store.list_annotations(run_id=run_root.name) == []
    assert store.list_annotations(run_id=run_root.name, include_deleted=True)[0]["deleted"] is True
    assert (run_root / "results.json").read_text(encoding="utf-8") == results_before
