from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


class ChemQAArtifactSupport:
    _ARCHIVABLE_ARTIFACT_FILENAMES = (
        "candidate_submission.json",
        "acceptance_decision.json",
        "submission_trace.json",
        "submission_cycles.json",
        "proposer_trajectory.json",
        "reviewer_trajectories.json",
        "review_statuses.json",
        "final_review_items.json",
        "final_answer.md",
        "final_submission.json",
        "qa_result.json",
    )

    def _candidate_protocol_dirs(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        candidates: list[Path] = []
        explicit_protocol = str(run_status.get("protocol_path") or "").strip()
        explicit_workspace_protocol = str(run_status.get("workspace_protocol_path") or "").strip()
        if explicit_protocol:
            explicit_parent = Path(explicit_protocol).expanduser().resolve().parent
            if self._is_allowed_protocol_source(explicit_parent, run_id=run_id):
                candidates.append(explicit_parent)
        if explicit_workspace_protocol:
            workspace_parent = Path(explicit_workspace_protocol).expanduser().resolve().parent
            if self._is_allowed_protocol_source(workspace_parent, run_id=run_id):
                candidates.append(workspace_parent)

        protocol_dir = self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id / "teams" / run_id
        candidates.append(protocol_dir)
        coordinator_slot = self._actual_slot_ids(self.slot_set)["debate-coordinator"]
        coordinator_workspace = getattr(self, "_active_slot_workspaces", {}).get(coordinator_slot)
        if coordinator_workspace is not None:
            candidates.append(coordinator_workspace)

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _is_allowed_protocol_source(self, path: Path, *, run_id: str) -> bool:
        candidate = path.expanduser().resolve(strict=False)
        allowed_roots = [
            self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id,
            *getattr(self, "_active_slot_workspaces", {}).values(),
        ]
        for root in allowed_roots:
            try:
                candidate.relative_to(Path(root).expanduser().resolve(strict=False))
                return True
            except ValueError:
                continue
        return False

    def _resolve_existing_qa_result(self, run_id: str, run_status: dict[str, Any]) -> Path | None:
        explicit_qa_result = str(run_status.get("qa_result_path") or "").strip()
        if explicit_qa_result:
            path = Path(explicit_qa_result).expanduser().resolve()
            if path.is_file():
                return path
        explicit_output_dir = str(run_status.get("artifacts_output_dir") or "").strip()
        candidate_dirs = []
        if explicit_output_dir:
            candidate_dirs.append(Path(explicit_output_dir).expanduser().resolve())
        candidate_dirs.append(self.chemqa_root / "generated" / "artifacts" / run_id)
        for directory in candidate_dirs:
            path = directory / "qa_result.json"
            if path.is_file():
                return path
        return None

    def _archive_dir(self, *, group_id: str, record_id: str, run_id: str) -> Path:
        return self.launch_workspace_root.parent / "artifacts" / group_id / self._slugify(record_id, limit=80) / run_id

    def _protocol_candidates_in_dir(self, source_dir: Path) -> tuple[Path, ...]:
        return (
            source_dir / "chemqa_review_protocol.yaml",
            source_dir / "chemqa_review_protocol.yml",
            source_dir / "chemqa_review_protocol.json",
            source_dir / "debate-coordinator" / "chemqa_review_protocol.yaml",
            source_dir / "debate-coordinator" / "chemqa_review_protocol.json",
            source_dir / "coordinator" / "chemqa_review_protocol.yaml",
            source_dir / "coordinator" / "chemqa_review_protocol.json",
        )

    def _resolve_protocol_file(self, run_id: str, run_status: dict[str, Any]) -> Path | None:
        explicit_candidates = []
        explicit_protocol = str(run_status.get("protocol_path") or "").strip()
        explicit_workspace_protocol = str(run_status.get("workspace_protocol_path") or "").strip()
        if explicit_protocol:
            candidate = Path(explicit_protocol).expanduser().resolve()
            if self._is_allowed_protocol_source(candidate.parent, run_id=run_id):
                explicit_candidates.append(candidate)
        if explicit_workspace_protocol:
            candidate = Path(explicit_workspace_protocol).expanduser().resolve()
            if self._is_allowed_protocol_source(candidate.parent, run_id=run_id):
                explicit_candidates.append(candidate)

        seen: set[str] = set()
        for candidate in explicit_candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate

        for source_dir in self._candidate_protocol_dirs(run_id, run_status):
            for candidate in self._protocol_candidates_in_dir(source_dir):
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                if candidate.is_file():
                    return candidate
        return None

    def _candidate_artifact_dirs(
        self,
        run_id: str,
        run_status: dict[str, Any],
        *,
        qa_result_path: Path | None = None,
    ) -> list[Path]:
        candidates: list[Path] = []
        if qa_result_path is not None:
            candidates.append(qa_result_path.expanduser().resolve().parent)
        explicit_output_dir = str(run_status.get("artifacts_output_dir") or "").strip()
        if explicit_output_dir:
            candidates.append(Path(explicit_output_dir).expanduser().resolve())
        candidates.append(self.chemqa_root / "generated" / "artifacts" / run_id)

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _copy_existing_artifacts(self, *, source_dir: Path, archive_dir: Path) -> None:
        if not source_dir.is_dir():
            return
        for filename in self._ARCHIVABLE_ARTIFACT_FILENAMES:
            source_path = source_dir / filename
            if not source_path.is_file():
                continue
            shutil.copy2(source_path, archive_dir / filename)
        for filename in (
            "final_answer_artifact.json",
            "failure_artifact.json",
            "artifact_manifest.json",
            "candidate_view.json",
            "validation_summary.json",
        ):
            source_path = source_dir / filename
            if source_path.is_file():
                shutil.copy2(source_path, archive_dir / filename)

    def _normalize_archived_qa_result(self, archive_dir: Path) -> dict[str, Any] | None:
        qa_result_path = archive_dir / "qa_result.json"
        if not qa_result_path.is_file():
            return None
        payload = json.loads(qa_result_path.read_text(encoding="utf-8"))
        artifact_paths = payload.get("artifact_paths")
        if not isinstance(artifact_paths, dict):
            artifact_paths = {}
        normalized_paths: dict[str, str] = {}
        key_to_filename = {
            "candidate_submission": "candidate_submission.json",
            "acceptance_decision": "acceptance_decision.json",
            "submission_trace": "submission_trace.json",
            "submission_cycles": "submission_cycles.json",
            "proposer_trajectory": "proposer_trajectory.json",
            "reviewer_trajectories": "reviewer_trajectories.json",
            "review_statuses": "review_statuses.json",
            "final_review_items": "final_review_items.json",
            "final_answer": "final_answer.md",
            "final_submission": "final_submission.json",
            "qa_result": "qa_result.json",
            "final_answer_artifact": "final_answer_artifact.json",
            "failure_artifact": "failure_artifact.json",
            "artifact_manifest": "artifact_manifest.json",
            "candidate_view": "candidate_view.json",
            "validation_summary": "validation_summary.json",
        }
        for key, filename in key_to_filename.items():
            candidate = archive_dir / filename
            if candidate.is_file():
                normalized_paths[key] = str(candidate)
            elif isinstance(artifact_paths.get(key), str) and str(artifact_paths.get(key)).strip():
                normalized_paths[key] = str(artifact_paths[key]).strip()
        payload["artifact_paths"] = normalized_paths
        qa_result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _archive_artifacts(
        self,
        *,
        run_id: str,
        group_id: str,
        record_id: str,
        run_status: dict[str, Any],
        env: dict[str, str],
        qa_result_path: Path | None = None,
    ) -> dict[str, Any]:
        archive_dir = self._archive_dir(group_id=group_id, record_id=record_id, run_id=run_id)
        archive_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        protocol_path = self._resolve_protocol_file(run_id, run_status)
        archived_protocol_path = ""
        if protocol_path is not None:
            archived_protocol = archive_dir / "chemqa_review_protocol.yaml"
            try:
                shutil.copy2(protocol_path, archived_protocol)
                archived_protocol_path = str(archived_protocol)
            except Exception as exc:
                errors.append(f"protocol_copy_failed: {exc}")

        for source_dir in self._candidate_artifact_dirs(run_id, run_status, qa_result_path=qa_result_path):
            try:
                self._copy_existing_artifacts(source_dir=source_dir, archive_dir=archive_dir)
            except Exception as exc:
                errors.append(f"artifact_copy_failed[{source_dir}]: {exc}")

        archived_qa_result_path = archive_dir / "qa_result.json"
        if not archived_qa_result_path.is_file() and protocol_path is not None:
            try:
                self._collect_artifacts_from_source(source_dir=protocol_path.parent, output_dir=archive_dir, env=env)
            except Exception as exc:
                errors.append(f"artifact_rebuild_failed: {exc}")

        normalized_qa_result = None
        if archived_qa_result_path.is_file():
            try:
                normalized_qa_result = self._normalize_archived_qa_result(archive_dir)
            except Exception as exc:
                errors.append(f"qa_result_normalization_failed: {exc}")

        archived_artifact_paths: dict[str, str] = {}
        for filename in self._ARCHIVABLE_ARTIFACT_FILENAMES:
            archived_path = archive_dir / filename
            if archived_path.is_file():
                archived_artifact_paths[archived_path.stem] = str(archived_path)
        for filename in (
            "final_answer_artifact.json",
            "failure_artifact.json",
            "artifact_manifest.json",
            "candidate_view.json",
            "validation_summary.json",
        ):
            archived_path = archive_dir / filename
            if archived_path.is_file():
                archived_artifact_paths[archived_path.stem] = str(archived_path)
        if normalized_qa_result is not None and isinstance(normalized_qa_result.get("artifact_paths"), dict):
            archived_artifact_paths.update(
                {str(key): str(value) for key, value in normalized_qa_result["artifact_paths"].items() if str(value).strip()}
            )

        has_protocol = bool(archived_protocol_path)
        has_qa_result = archived_qa_result_path.is_file()
        if has_protocol and has_qa_result:
            archive_status = "ok"
        elif has_protocol:
            archive_status = "protocol_only"
        elif archived_artifact_paths:
            archive_status = "artifacts_only"
        elif errors:
            archive_status = "error"
        else:
            archive_status = "missing"

        return {
            "archive_dir": str(archive_dir),
            "archived_protocol_path": archived_protocol_path,
            "archived_artifact_paths": archived_artifact_paths,
            "artifact_archive_status": archive_status,
            "artifact_archive_error": "\n".join(errors).strip(),
            "qa_result_path": str(archived_qa_result_path) if archived_qa_result_path.is_file() else "",
        }

    def _candidate_submission_paths(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        import re

        candidates: list[Path] = []
        for root in self._candidate_protocol_dirs(run_id, run_status):
            if not root.exists():
                continue
            for path in root.rglob("proposer-1.md"):
                if "proposals" not in path.parts:
                    continue
                candidates.append(path.resolve())

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)

        def sort_key(path: Path) -> tuple[int, float, str]:
            match = re.search(r"epoch-(\d+)", str(path))
            epoch = int(match.group(1)) if match else -1
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (epoch, mtime, str(path))

        return sorted(deduped, key=sort_key, reverse=True)

    def _build_candidate_submission_fallback(self, run_id: str, run_status: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        for proposal_path in self._candidate_submission_paths(run_id, run_status):
            proposal_payload = self._load_yaml_mapping(proposal_path)
            if not proposal_payload:
                continue
            short_answer_text, full_response_text = self._build_chemqa_response_from_submission(final_submission=proposal_payload)
            if short_answer_text:
                return short_answer_text, full_response_text, {
                    "fallback_source": "proposer-1-proposal",
                    "proposal_path": str(proposal_path),
                    "proposal_payload": proposal_payload,
                }

        preview = self._normalize_space(str(run_status.get("final_answer_preview") or ""))
        if preview:
            return preview, f"FINAL ANSWER: {preview}", {
                "fallback_source": "run-status-final-answer-preview",
            }
        return None

    def _assess_recovered_answer(
        self,
        *,
        run_id: str,
        run_status: dict[str, Any],
        archive_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        projection = self._failure_artifact_answer_projection(run_status=run_status, archive_meta=archive_meta)
        if projection is not None:
            return projection
        fallback_payload = self._build_candidate_submission_fallback(run_id, run_status)
        if fallback_payload is None:
            return None
        short_answer_text, full_response_text, fallback_meta = fallback_payload
        short_text = self._normalize_space(short_answer_text)
        if not short_text:
            return {
                "evaluable": False,
                "scored": False,
                "reliability": "none",
                "recovery_mode": str(fallback_meta.get("fallback_source") or "none"),
                "reason": "empty_short_answer",
                "short_answer_text": "",
                "full_response_text": full_response_text,
                "details": fallback_meta,
            }
        recovery_mode = str(fallback_meta.get("fallback_source") or "candidate_submission")
        if recovery_mode == "run-status-final-answer-preview":
            return {
                "evaluable": False,
                "scored": False,
                "reliability": "low_confidence_recovered",
                "recovery_mode": recovery_mode,
                "reason": "preview_requires_strict_validation",
                "short_answer_text": short_text,
                "full_response_text": full_response_text,
                "details": fallback_meta,
            }
        return {
            "evaluable": True,
            "scored": True,
            "reliability": "high_confidence_recovered",
            "recovery_mode": recovery_mode,
            "reason": "",
            "short_answer_text": short_text,
            "full_response_text": full_response_text,
            "details": fallback_meta,
        }

    def _failure_artifact_answer_projection(
        self,
        *,
        run_status: dict[str, Any],
        archive_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for key in ("failure_artifact_path",):
            value = str(run_status.get(key) or "").strip()
            if value:
                candidates.append(Path(value).expanduser())
        archived_paths = archive_meta.get("archived_artifact_paths")
        if isinstance(archived_paths, dict):
            value = str(archived_paths.get("failure_artifact") or "").strip()
            if value:
                candidates.append(Path(value).expanduser())
        qa_result_path = str(archive_meta.get("qa_result_path") or run_status.get("qa_result_path") or "").strip()
        payloads: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        if qa_result_path:
            path = Path(qa_result_path).expanduser()
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    payloads.append(payload)

        for payload in payloads:
            projection = payload.get("answer_projection")
            recovery = payload.get("recovery_eligibility")
            if not isinstance(projection, dict) or not isinstance(recovery, dict):
                continue
            short_text = self._normalize_space(
                str(
                    projection.get("evaluator_answer")
                    or projection.get("direct_answer")
                    or projection.get("answer")
                    or projection.get("value")
                    or ""
                )
            )
            full_text = str(projection.get("full_answer") or projection.get("display_answer") or short_text).strip()
            if short_text and full_text == short_text:
                full_text = f"FINAL ANSWER: {short_text}"
            return {
                "evaluable": bool(recovery.get("evaluable")),
                "scored": bool(recovery.get("scored")),
                "reliability": str(recovery.get("reliability") or "none"),
                "recovery_mode": str(recovery.get("recovery_mode") or "failure_artifact_answer_projection"),
                "reason": str(recovery.get("reason") or ""),
                "short_answer_text": short_text,
                "full_response_text": full_text,
                "details": {
                    "fallback_source": "failure_artifact",
                    "answer_projection": self._deep_copy_jsonish(projection),
                    "recovery_eligibility": self._deep_copy_jsonish(recovery),
                },
            }
        return None

    def _collect_artifacts_from_source(self, *, source_dir: Path, output_dir: Path, env: dict[str, str]) -> None:
        command = [
            self._current_python(),
            str(self.collect_script),
            "--skill-root",
            str(self.chemqa_root),
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(output_dir),
        ]
        answer_kind = str(env.get("CHEMQA_ANSWER_KIND") or "").strip()
        if answer_kind:
            command.extend(["--answer-kind", answer_kind])
        result = self._run_subprocess(command, env=env, cwd=self.chemqa_root, timeout=120)
        self._parse_json_stdout(result, command)

    def _load_archived_completed_qa_result(self, archive_meta: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
        archived_qa_result = str(archive_meta.get("qa_result_path") or "").strip()
        if not archived_qa_result:
            return None
        qa_result_path = Path(archived_qa_result).expanduser()
        if not qa_result_path.is_file():
            return None
        payload = json.loads(qa_result_path.read_text(encoding="utf-8"))
        if str(payload.get("terminal_state") or "").strip() != "completed":
            return None
        return qa_result_path.resolve(), payload

    def _ensure_artifacts(
        self,
        run_id: str,
        *,
        env: dict[str, str],
        run_status: dict[str, Any],
        wait_seconds: int = 120,
        poll_seconds: int = 5,
    ) -> Path:
        import time

        deadline = time.time() + wait_seconds
        last_seen_status = run_status
        checked_sources: list[str] = []
        while time.time() < deadline:
            last_seen_status = self._read_run_status(run_id) or last_seen_status
            qa_result_path = self._resolve_existing_qa_result(run_id, last_seen_status)
            if qa_result_path is not None:
                return qa_result_path

            output_dir = Path(
                str(last_seen_status.get("artifacts_output_dir") or (self.chemqa_root / "generated" / "artifacts" / run_id))
            ).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

            for source_dir in self._candidate_protocol_dirs(run_id, last_seen_status):
                checked_sources.append(str(source_dir))
                if (source_dir / "chemqa_review_protocol.yaml").is_file() or (source_dir / "chemqa_review_protocol.yml").is_file():
                    self._collect_artifacts_from_source(source_dir=source_dir, output_dir=output_dir, env=env)
                    qa_result_path = output_dir / "qa_result.json"
                    if qa_result_path.is_file():
                        return qa_result_path
            time.sleep(poll_seconds)

        error_message = (
            f"ChemQA run `{run_id}` reached terminal state but artifacts were not resolved within {wait_seconds}s. "
            f"Last run status: {last_seen_status}. Checked sources: {checked_sources}"
        )
        if self._benchmark_error_factory is not None:
            raise self._benchmark_error_factory(error_message)
        raise RuntimeError(error_message)
