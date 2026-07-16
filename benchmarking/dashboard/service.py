from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import runtime_paths

from benchmarking.dashboard.annotations import AnnotationStore
from benchmarking.dashboard.progress import load_progress


class DashboardError(RuntimeError):
    pass


class RunNotFoundError(DashboardError):
    pass


class RecordNotFoundError(DashboardError):
    pass


class AssetAccessError(DashboardError):
    pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_load_json(path: Path) -> Any:
    try:
        return _load_json(path)
    except Exception:
        return {}


def _slug_variants(value: str) -> set[str]:
    stripped = str(value or "").strip()
    return {stripped, stripped.replace("_", "-"), stripped.replace("-", "_")}


def _format_number(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{float(value):.4g}"


def _dashboard_dataset_subset(result: dict[str, Any]) -> tuple[str, str]:
    dataset = str(result.get("dataset") or "")
    subset = str(result.get("subset") or "")
    if dataset.startswith("verifier_grounded_"):
        return "vgb", subset or dataset
    return dataset, subset


def _score_label(result: dict[str, Any]) -> str:
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    status = {key: result.get(key) for key in ("evaluable", "scored")}
    if status.get("evaluable") is False:
        return "不可评测"
    if status.get("scored") is False:
        return "未评分"
    primary_metric = str(evaluation.get("primary_metric") or "")
    if primary_metric == "verifier_score":
        return f"Verifier {_format_number(evaluation.get('normalized_score', evaluation.get('score')))}".strip()
    if primary_metric == "rubric_points":
        return f"{_format_number(evaluation.get('score'))}/{_format_number(evaluation.get('max_score'))}"
    details = evaluation.get("details") if isinstance(evaluation.get("details"), dict) else {}
    if primary_metric == "answer_accuracy" and "rpf" in details:
        return f"{'正确' if evaluation.get('passed') is True else '错误'}; RPF {_format_number(float(details.get('rpf')) * 100)}%"
    passed = evaluation.get("passed")
    if passed is True:
        return "正确"
    if passed is False:
        return "错误"
    return "已评分" if result.get("scored") else "未知"


def _outcome(result: dict[str, Any]) -> str:
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    if result.get("evaluable") is False or result.get("scored") is False:
        return "not_scored"
    if evaluation.get("passed") is True:
        return "passed"
    if evaluation.get("passed") is False:
        return "failed"
    return "scored"


def _verifier_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
    if evaluation.get("primary_metric") != "verifier_score" and result.get("eval_kind") != "verifier_grounded":
        return None
    details = evaluation.get("details") if isinstance(evaluation.get("details"), dict) else {}
    return {
        "status": details.get("status"),
        "failure_type": details.get("failure_type"),
        "message": details.get("message"),
        "canonical_smiles": details.get("canonical_smiles"),
        "properties": details.get("properties") or {},
        "constraint_scores": details.get("constraint_scores") or [],
        "versions": details.get("versions") or {},
    }


def _group_sort_key(group: dict[str, Any]) -> tuple[int, str]:
    group_id = str(group.get("group_id") or "")
    preferred = {
        "single_llm_skills_on": 0,
        "single_llm_skills_off": 1,
        "chemqa_skills_on": 2,
    }
    return (preferred.get(group_id, 100), group_id)


def _audit_int(audit: dict[str, Any], key: str) -> int:
    value = audit.get(key)
    return int(value) if isinstance(value, (int, float)) else 0


def _diagnostics_payload(result: dict[str, Any], skill_audit: dict[str, Any]) -> dict[str, Any]:
    skills_enabled = bool(result.get("skills_enabled", False))
    legacy_skill_calls = _audit_int(skill_audit, "skill_tool_call_count")
    legacy_skill_failures = _audit_int(skill_audit, "skill_tool_failure_count")
    has_exec_call_count = isinstance(skill_audit.get("exec_tool_call_count"), (int, float))
    has_exec_failure_count = isinstance(skill_audit.get("exec_tool_failure_count"), (int, float))
    exec_call_count = _audit_int(skill_audit, "exec_tool_call_count")
    exec_failure_count = _audit_int(skill_audit, "exec_tool_failure_count")
    if not has_exec_call_count:
        exec_call_count = legacy_skill_calls
    if not has_exec_failure_count:
        exec_failure_count = legacy_skill_failures
    return {
        "elapsed_seconds": result.get("elapsed_seconds"),
        "openclaw_tool_call_count": skill_audit.get("openclaw_tool_call_count", skill_audit.get("tool_call_count")),
        "exec_tool_call_count": exec_call_count,
        "exec_tool_failure_count": exec_failure_count,
        "skill_tool_call_count": legacy_skill_calls if skills_enabled else 0,
        "skill_tool_failure_count": legacy_skill_failures if skills_enabled else 0,
        "coverage_checklist_present": skill_audit.get("coverage_checklist_present"),
    }


def _workspace_isolation_payload(runner_meta: dict[str, Any]) -> dict[str, Any]:
    isolation = runner_meta.get("workspace_isolation")
    if not isinstance(isolation, dict):
        return {}
    if "adjudication" in isolation:
        return {
            key: isolation.get(key)
            for key in (
                "policy_digest",
                "audit_execution_status",
                "boundary_status",
                "contamination_status",
                "adjudication",
                "findings",
                "cleanup",
            )
        }
    legacy_status = str(isolation.get("audit_status") or "").strip()
    if not legacy_status:
        return {}
    return {
        "legacy_schema": True,
        "audit_execution_status": "unavailable" if legacy_status == "unavailable" else "complete",
        "boundary_status": "clean" if legacy_status == "clean" else "unknown",
        "contamination_status": "clear" if legacy_status == "clean" else "indeterminate",
        "adjudication": "scoreable" if legacy_status == "clean" else "non_evaluable",
        "findings": isolation.get("findings") or [],
        "cleanup": isolation.get("cleanup") or {},
    }


class BenchmarkDashboard:
    def __init__(
        self,
        *,
        run_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
        annotation_store: AnnotationStore | None = None,
    ) -> None:
        self.run_roots = [
            Path(root).expanduser().resolve()
            for root in (run_roots or [runtime_paths.project_state_root / "benchmark-runs"])
        ]
        self.annotation_store = annotation_store or AnnotationStore(runtime_paths.project_state_root / "benchmark-dashboard" / "dashboard.sqlite")

    def _candidate_run_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        for root in self.run_roots:
            if not root.exists():
                continue
            if self._looks_like_run(root):
                candidates.append(root)
                continue
            candidates.extend(path for path in root.iterdir() if path.is_dir() and self._looks_like_run(path))
        return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)

    @staticmethod
    def _looks_like_run(path: Path) -> bool:
        return any((path / name).exists() for name in ("results.json", "runtime-manifest.json", "per-record", "waves", "progress"))

    def _run_dir(self, run_id: str) -> Path:
        for path in self._candidate_run_dirs():
            if path.name == run_id:
                return path
        raise RunNotFoundError(f"Unknown benchmark run: {run_id}")

    def _load_results(self, run_root: Path) -> list[dict[str, Any]]:
        results_path = run_root / "results.json"
        if results_path.is_file():
            payload = _safe_load_json(results_path)
            results = payload.get("results") if isinstance(payload, dict) else []
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        per_record_root = run_root / "per-record"
        results: list[dict[str, Any]] = []
        if per_record_root.is_dir():
            for path in sorted(per_record_root.glob("*/*.json")):
                loaded = _safe_load_json(path)
                if isinstance(loaded, dict):
                    results.append(loaded)
        return results

    def _run_payload(self, run_root: Path) -> dict[str, Any]:
        payload = _safe_load_json(run_root / "results.json") if (run_root / "results.json").is_file() else {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _group_ids(run_payload: dict[str, Any], results: list[dict[str, Any]], run_root: Path) -> list[str]:
        groups_payload = run_payload.get("groups") if isinstance(run_payload.get("groups"), list) else []
        group_ids = [str(group.get("id") or "") for group in groups_payload if isinstance(group, dict) and group.get("id")]
        if not group_ids:
            group_ids = sorted({str(result.get("group_id") or "") for result in results if result.get("group_id")})
        if not group_ids and (run_root / "per-record").is_dir():
            group_ids = sorted(path.name for path in (run_root / "per-record").iterdir() if path.is_dir())
        return group_ids

    def list_runs(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        metadata = self.annotation_store.list_run_metadata()
        runs: list[dict[str, Any]] = []
        for run_root in self._candidate_run_dirs():
            run_id = run_root.name
            meta = metadata.get(run_id, {})
            if meta.get("hidden") and not include_hidden:
                continue
            payload = self._run_payload(run_root)
            results = self._load_results(run_root)
            group_ids = self._group_ids(payload, results, run_root)
            record_ids = sorted({str(result.get("record_id") or "") for result in results if result.get("record_id")})
            display_pairs = [_dashboard_dataset_subset(result) for result in results]
            datasets = sorted({dataset for dataset, _subset in display_pairs if dataset})
            subsets = sorted({subset for _dataset, subset in display_pairs if subset})
            total = int(payload.get("records") or len(record_ids)) * max(1, len(group_ids))
            progress = load_progress(run_root, expected_total=total, group_ids=group_ids)
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            runs.append(
                {
                    "run_id": run_id,
                    "alias": meta.get("alias", ""),
                    "favorite": bool(meta.get("favorite", False)),
                    "hidden": bool(meta.get("hidden", False)),
                    "path": str(run_root),
                    "generated_at": payload.get("generated_at", ""),
                    "updated_at": progress.get("updated_at") or payload.get("generated_at", ""),
                    "status": progress.get("status") or ("completed" if (run_root / "results.json").is_file() else "pending"),
                    "record_count": int(payload.get("records") or len(record_ids)),
                    "group_count": len(group_ids),
                    "dataset_files": payload.get("dataset_files") or [],
                    "datasets": datasets,
                    "subsets": subsets,
                    "progress": progress,
                    "summary": summary,
                }
            )
        return runs

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_root = self._run_dir(run_id)
        payload = self._run_payload(run_root)
        results = self._load_results(run_root)
        group_ids = self._group_ids(payload, results, run_root)
        record_ids = sorted({str(result.get("record_id") or "") for result in results if result.get("record_id")})
        total = int(payload.get("records") or len(record_ids)) * max(1, len(group_ids))
        meta = self.annotation_store.get_run_metadata(run_id) or {}
        return {
            "run_id": run_id,
            "alias": meta.get("alias", ""),
            "favorite": bool(meta.get("favorite", False)),
            "hidden": bool(meta.get("hidden", False)),
            "path": str(run_root),
            "payload": payload,
            "progress": load_progress(run_root, expected_total=total, group_ids=group_ids),
            "annotations": self.annotation_store.list_annotations(run_id=run_id),
        }

    def list_records(self, run_id: str) -> list[dict[str, Any]]:
        run_root = self._run_dir(run_id)
        results = self._load_results(run_root)
        annotations = self.annotation_store.list_annotations(run_id=run_id)
        annotation_count: dict[str, int] = {}
        for annotation in annotations:
            annotation_count[str(annotation["record_id"])] = annotation_count.get(str(annotation["record_id"]), 0) + 1
        by_record: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            by_record.setdefault(str(result.get("record_id") or ""), []).append(result)
        records: list[dict[str, Any]] = []
        for record_id, items in sorted(by_record.items()):
            first = items[0]
            dataset, subset = _dashboard_dataset_subset(first)
            records.append(
                {
                    "record_id": record_id,
                    "dataset": dataset,
                    "subset": subset,
                    "eval_kind": first.get("eval_kind", ""),
                    "prompt_preview": str(first.get("prompt") or "")[:320],
                    "group_results": [
                        {
                            "group_id": item.get("group_id", ""),
                            "score_label": _score_label(item),
                            "outcome": _outcome(item),
                            "elapsed_seconds": item.get("elapsed_seconds"),
                        }
                        for item in sorted(items, key=_group_sort_key)
                    ],
                    "annotation_count": annotation_count.get(record_id, 0),
                }
            )
        return records

    def _find_record_results(self, run_root: Path, record_id: str) -> list[dict[str, Any]]:
        variants = _slug_variants(record_id)
        results = [
            result
            for result in self._load_results(run_root)
            if str(result.get("record_id") or "") in variants
        ]
        if results:
            return results
        per_record_root = run_root / "per-record"
        found: list[dict[str, Any]] = []
        if per_record_root.is_dir():
            for path in sorted(per_record_root.glob("*/*.json")):
                if path.stem in variants:
                    loaded = _safe_load_json(path)
                    if isinstance(loaded, dict):
                        found.append(loaded)
        return found

    def _runtime_bundle(self, run_root: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
        for result in results:
            runner_meta = result.get("runner_meta") if isinstance(result.get("runner_meta"), dict) else {}
            bundle = runner_meta.get("runtime_bundle") if isinstance(runner_meta.get("runtime_bundle"), dict) else {}
            if bundle:
                return bundle
        return {}

    def _question_markdown(self, run_root: Path, results: list[dict[str, Any]]) -> tuple[str, str]:
        bundle = self._runtime_bundle(run_root, results)
        question_path_raw = str(bundle.get("question_markdown") or "").strip()
        if question_path_raw:
            question_path = Path(question_path_raw).expanduser().resolve()
            if self._is_within_run(run_root, question_path) and question_path.is_file():
                return question_path.read_text(encoding="utf-8", errors="replace"), str(question_path.relative_to(run_root.resolve()))
        first = results[0] if results else {}
        return str(first.get("prompt") or ""), ""

    def _assets(self, run_root: Path, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bundle = self._runtime_bundle(run_root, results)
        assets: list[dict[str, Any]] = []
        for raw in bundle.get("image_files") or []:
            path = Path(str(raw)).expanduser().resolve()
            if not self._is_within_run(run_root, path) or not path.is_file():
                continue
            relative = str(path.relative_to(run_root.resolve()))
            assets.append(
                {
                    "relative_path": relative,
                    "url": f"/api/runs/{run_root.name}/assets/{relative}",
                    "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                }
            )
        return assets

    def get_record(self, run_id: str, record_id: str) -> dict[str, Any]:
        run_root = self._run_dir(run_id)
        results = self._find_record_results(run_root, record_id)
        if not results:
            raise RecordNotFoundError(f"Unknown record `{record_id}` in run `{run_id}`")
        results = sorted(results, key=_group_sort_key)
        first = results[0]
        dataset, subset = _dashboard_dataset_subset(first)
        question_markdown, question_source = self._question_markdown(run_root, results)
        groups: list[dict[str, Any]] = []
        for result in results:
            runner_meta = result.get("runner_meta") if isinstance(result.get("runner_meta"), dict) else {}
            skill_audit = runner_meta.get("skill_use_audit") if isinstance(runner_meta.get("skill_use_audit"), dict) else {}
            groups.append(
                {
                    "group_id": result.get("group_id", ""),
                    "group_label": result.get("group_label", ""),
                    "runner": result.get("runner", ""),
                    "skills_enabled": result.get("skills_enabled", False),
                    "answer_text": result.get("answer_text", ""),
                    "short_answer_text": result.get("short_answer_text", ""),
                    "full_response_text": result.get("full_response_text", ""),
                    "evaluation": result.get("evaluation") or {},
                    "score_label": _score_label(result),
                    "outcome": _outcome(result),
                    "verifier": _verifier_payload(result),
                    "status_axes": {
                        "run_lifecycle_status": result.get("run_lifecycle_status"),
                        "protocol_completion_status": result.get("protocol_completion_status"),
                        "answer_availability": result.get("answer_availability"),
                        "answer_reliability": result.get("answer_reliability"),
                        "evaluable": result.get("evaluable"),
                        "scored": result.get("scored"),
                        "recovery_mode": result.get("recovery_mode"),
                        "degraded_execution": result.get("degraded_execution"),
                        "execution_error_kind": result.get("execution_error_kind"),
                        "error": result.get("error"),
                    },
                    "diagnostics": _diagnostics_payload(result, skill_audit),
                    "workspace_isolation": _workspace_isolation_payload(runner_meta),
                    "annotations": self.annotation_store.list_annotations(
                        run_id=run_id,
                        record_id=str(result.get("record_id") or record_id),
                        group_id=str(result.get("group_id") or ""),
                    ),
                }
            )
        return {
            "run_id": run_id,
            "record_id": first.get("record_id") or record_id,
            "dataset": dataset,
            "subset": subset,
            "eval_kind": first.get("eval_kind", ""),
            "prompt": first.get("prompt", ""),
            "question_markdown": question_markdown,
            "question_source": question_source,
            "reference_answer": first.get("reference_answer", ""),
            "reference": self._reference_payload(first),
            "assets": self._assets(run_root, results),
            "groups": groups,
            "annotations": self.annotation_store.list_annotations(run_id=run_id, record_id=str(first.get("record_id") or record_id)),
        }

    @staticmethod
    def _reference_payload(result: dict[str, Any]) -> dict[str, Any]:
        evaluation = result.get("evaluation") if isinstance(result.get("evaluation"), dict) else {}
        details = evaluation.get("details") if isinstance(evaluation.get("details"), dict) else {}
        checkpoints = details.get("checkpoint_matches") or details.get("items") or []
        return {
            "answer": result.get("reference_answer", ""),
            "reasoning": details.get("reference_reasoning") or "",
            "checkpoints": checkpoints,
            "judge": details.get("judge") or {},
            "available": bool(result.get("reference_answer") or details.get("reference_reasoning") or checkpoints),
        }

    @staticmethod
    def _is_within_run(run_root: Path, candidate: Path) -> bool:
        root = run_root.resolve()
        try:
            candidate.resolve().relative_to(root)
            return True
        except ValueError:
            return False

    def resolve_asset(self, run_id: str, asset_path: str) -> Path:
        run_root = self._run_dir(run_id)
        candidate = (run_root / asset_path).resolve()
        if not self._is_within_run(run_root, candidate):
            raise AssetAccessError("Asset path escapes benchmark run directory")
        if not candidate.is_file():
            raise AssetAccessError(f"Asset does not exist: {asset_path}")
        return candidate
