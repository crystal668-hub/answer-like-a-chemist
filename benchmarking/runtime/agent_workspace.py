from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping

from benchmarking.core.contracts import FailureInfo
from benchmarking.runtime.workspace_audit import (
    _audit_recovery_candidates,
    _forbidden_access_findings,
    _operation_outcome,
    _redact_text,
    _select_audit_transcript,
    _tool_events_from_transcript,
    _transcript_audit_failure,
    _workdir_fallback_finding,
)
from benchmarking.runtime.workspace_policy import (
    ContaminationAudit as _ContaminationAudit,
    ProtectedRoot as _ProtectedRoot,
    WORKSPACE_ISOLATION_SCHEMA_VERSION as _WORKSPACE_ISOLATION_SCHEMA_VERSION,
    WorkspaceAccessPolicy as _WorkspaceAccessPolicy,
    WorkspaceAudit as _WorkspaceAudit,
    _normalize_protected_roots,
    _path_is_within,
    adjudicate_workspace_findings as _adjudicate_workspace_findings,
    build_workspace_access_policy as _build_workspace_access_policy,
    ensure_workspace_audit as _ensure_workspace_audit,
)


SENTINEL_FILENAME = ".benchmark-workspace.json"
SENTINEL_KIND = "openclaw-benchmark-attempt-workspace"
ARCHIVE_KIND = "openclaw-benchmark-workspace-archive"
SCHEMA_VERSION = 1
SCRATCH_CONTRACT_VERSION = 2
WORKSPACE_FAILURE_LAYER = "benchmark_runtime"
WORKSPACE_FAILURE_SOURCE = "workspace_isolation"

WORKSPACE_FAILURE_CODES = frozenset(
    {
        "workspace_path_unsafe",
        "workspace_sentinel_invalid",
        "workspace_lock_conflict",
        "workspace_template_invalid",
        "workspace_prepare_failed",
        "workspace_archive_failed",
        "benchmark_workspace_contamination",
        "workspace_recovery_failed",
        "workspace_policy_invalid",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def workspace_slug(value: str, *, limit: int = 64) -> str:
    raw = str(value or "item").strip() or "item"
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower() or "item"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    stem_limit = max(1, limit - len(digest) - 1)
    return f"{cleaned[:stem_limit].rstrip('-') or 'item'}-{digest}"


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_stats(root: Path) -> tuple[int, int]:
    file_count = 0
    total_bytes = 0
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode):
            file_count += 1
            total_bytes += path.lstat().st_size
    return file_count, total_bytes


@dataclass(frozen=True)
class WorkspaceTemplate:
    template_id: str
    source_dir: Path | None = None
    files: Mapping[str, Path] = field(default_factory=dict)
    agents_base: Path | None = None
    agents_overlay: Path | None = None



@dataclass(frozen=True)
class AttemptIdentity:
    run_id: str
    invocation_id: str
    group_id: str
    runner_kind: str
    agent_id: str
    record_id: str
    attempt_index: int
    session_id: str
    template_id: str

    def __post_init__(self) -> None:
        required = (
            "run_id",
            "invocation_id",
            "group_id",
            "runner_kind",
            "agent_id",
            "record_id",
            "session_id",
            "template_id",
        )
        for name in required:
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"Attempt identity field `{name}` must be non-empty.")
        if self.attempt_index < 0:
            raise ValueError("Attempt identity attempt_index must be non-negative.")

    def sentinel_fields(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "group_id": self.group_id,
            "runner_kind": self.runner_kind,
            "agent_id": self.agent_id,
            "record_id": self.record_id,
            "attempt_index": self.attempt_index,
            "session_id": self.session_id,
            "template_id": self.template_id,
        }



@dataclass(frozen=True)
class AttemptOutcome:
    runner_status: str
    archive_reason: str = "attempt_terminal"
    contamination_audit: _WorkspaceAudit | _ContaminationAudit = field(default_factory=_WorkspaceAudit)

    def __post_init__(self) -> None:
        if self.runner_status not in {"completed", "recovered", "failed", "aborted"}:
            raise ValueError(f"Unsupported workspace outcome status: {self.runner_status}")


@dataclass(frozen=True)
class WorkspaceArchive:
    workspace: Path
    manifest_path: Path
    manifest_sha256: str
    payload: dict[str, Any]

    def to_meta(self) -> dict[str, Any]:
        return {
            "archive_workspace": str(self.workspace),
            "archive_manifest": str(self.manifest_path),
            "archive_manifest_sha256": self.manifest_sha256,
            "archive_ok": True,
        }


@dataclass
class AttemptWorkspaceLease:
    identity: AttemptIdentity
    active_workspace: Path
    scratch_dir: Path
    request_dir: Path
    output_dir: Path
    notes_dir: Path
    tmp_dir: Path
    sentinel_path: Path
    lock_path: Path
    template_sha256: str
    created_at: str
    _lock_handle: BinaryIO = field(repr=False, compare=False)
    archive: WorkspaceArchive | None = field(default=None, repr=False, compare=False)
    released: bool = field(default=False, repr=False, compare=False)

    def to_meta(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": _WORKSPACE_ISOLATION_SCHEMA_VERSION,
            **self.identity.sentinel_fields(),
            "active_workspace": str(self.active_workspace),
            "template_sha256": self.template_sha256,
            "preflight_ok": True,
            "archive_ok": False,
            "scratch_contract_version": SCRATCH_CONTRACT_VERSION,
        }
        if self.archive is not None:
            payload.update(self.archive.to_meta())
        return payload


@dataclass(frozen=True)
class WorkspaceLeaseSet:
    leases: tuple[AttemptWorkspaceLease, ...]

    def by_agent_id(self) -> dict[str, AttemptWorkspaceLease]:
        return {lease.identity.agent_id: lease for lease in self.leases}

    def to_meta(self) -> dict[str, Any]:
        return {
            "schema_version": _WORKSPACE_ISOLATION_SCHEMA_VERSION,
            "preflight_ok": True,
            "archive_ok": False,
            "findings": [],
            "scratch_contract_version": SCRATCH_CONTRACT_VERSION,
            "slots": {lease.identity.agent_id: lease.to_meta() for lease in self.leases},
        }


class WorkspaceIsolationError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        if code not in WORKSPACE_FAILURE_CODES:
            raise ValueError(f"Unsupported workspace isolation failure code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = {
            "code": code,
            "layer": WORKSPACE_FAILURE_LAYER,
            "source": WORKSPACE_FAILURE_SOURCE,
            "retryable": False,
            **dict(details or {}),
        }

    def to_failure_info(self) -> FailureInfo:
        return FailureInfo(code=self.code, message=self.message, details=dict(self.details))


class AttemptWorkspaceManager:
    def __init__(
        self,
        *,
        runtime_root: Path,
        output_root: Path,
        run_id: str,
        invocation_id: str,
        templates: Mapping[str, WorkspaceTemplate],
        protected_roots: tuple[_ProtectedRoot, ...] | list[_ProtectedRoot],
    ) -> None:
        self.runtime_root = runtime_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.run_id = str(run_id)
        self.invocation_id = str(invocation_id)
        self.templates = dict(templates)
        try:
            self.protected_roots = _normalize_protected_roots(protected_roots)
        except ValueError as exc:
            raise WorkspaceIsolationError(
                "workspace_policy_invalid",
                str(exc),
            ) from exc
        self.run_runtime_root = self.runtime_root / workspace_slug(self.run_id)
        self.invocation_runtime_root = self.run_runtime_root / workspace_slug(self.invocation_id)
        self.active_root = self.invocation_runtime_root / "active"
        self.locks_root = self.invocation_runtime_root / "locks"
        self.archive_root = self.output_root / "agent-workspace-archives"
        self.quarantine_root = self.output_root / "agent-workspace-quarantine"

    def active_workspace_path(self, *, group_id: str, agent_id: str, invocation_id: str | None = None) -> Path:
        invocation_root = self.run_runtime_root / workspace_slug(invocation_id or self.invocation_id)
        return invocation_root / "active" / workspace_slug(group_id) / workspace_slug(agent_id)

    def lock_path(self, *, group_id: str, agent_id: str, invocation_id: str | None = None) -> Path:
        invocation_root = self.run_runtime_root / workspace_slug(invocation_id or self.invocation_id)
        name = f"{workspace_slug(group_id)}--{workspace_slug(agent_id)}.lock"
        return invocation_root / "locks" / name

    def template_manifest(self) -> dict[str, dict[str, str]]:
        manifest: dict[str, dict[str, str]] = {}
        for template_id, template in sorted(self.templates.items()):
            _, digest = self._template_entries(template)
            manifest[template_id] = {"template_id": template_id, "template_sha256": digest}
        return manifest

    def forbidden_path_policy_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "protected_roots": [
                {
                    "policy_id": root.policy_id,
                    "path": str(root.path),
                    "source": root.source,
                }
                for root in self.protected_roots
            ],
        }

    def policy_for_lease(
        self,
        lease: AttemptWorkspaceLease,
        *,
        role: str,
        skills_enabled: bool,
        always_read_scopes: tuple[Path, ...] | list[Path] = (),
        read_scopes: tuple[Path, ...] | list[Path] = (),
        write_scopes: tuple[Path, ...] | list[Path] = (),
        exec_workdir_scopes: tuple[Path, ...] | list[Path] = (),
    ) -> _WorkspaceAccessPolicy:
        return _build_workspace_access_policy(
            active_workspace=lease.active_workspace,
            role=role,
            skills_enabled=bool(skills_enabled),
            protected_roots=self.protected_roots,
            always_read_scopes=always_read_scopes,
            skill_read_scopes=read_scopes,
            extra_write_scopes=write_scopes,
            extra_exec_workdir_scopes=exec_workdir_scopes,
        )

    def prepare(self, identity: AttemptIdentity) -> AttemptWorkspaceLease:
        self._validate_identity_scope(identity)
        template = self.templates.get(identity.template_id)
        if template is None:
            raise WorkspaceIsolationError(
                "workspace_template_invalid",
                f"Unknown benchmark workspace template `{identity.template_id}`.",
                details={"template_id": identity.template_id},
            )
        entries, template_sha256 = self._template_entries(template)
        active_workspace = self.active_workspace_path(group_id=identity.group_id, agent_id=identity.agent_id)
        lock_path = self.lock_path(group_id=identity.group_id, agent_id=identity.agent_id)
        self._ensure_runtime_layout(active_workspace=active_workspace, lock_path=lock_path)
        lock_handle = self._acquire_lock(lock_path, identity)
        temporary: Path | None = None
        try:
            if active_workspace.is_symlink():
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Active benchmark workspace path is a symlink.",
                    details={"workspace_path": str(active_workspace)},
                )
            if active_workspace.exists():
                return self._resume_or_reject_existing(
                    identity=identity,
                    active_workspace=active_workspace,
                    lock_path=lock_path,
                    lock_handle=lock_handle,
                    template_sha256=template_sha256,
                    template_entries=entries,
                )

            temporary = active_workspace.parent / f".{active_workspace.name}.prepare-{uuid.uuid4().hex}"
            temporary.mkdir(mode=0o700)
            self._materialize_template(temporary, entries)
            scratch_dir = self._scratch_dir(temporary, identity)
            request_dir = scratch_dir / "requests"
            output_dir = scratch_dir / "outputs"
            notes_dir = scratch_dir / "notes"
            tmp_dir = scratch_dir / "tmp"
            for path in (request_dir, output_dir, notes_dir, tmp_dir):
                path.mkdir(parents=True, exist_ok=False)
            created_at = _utc_now()
            sentinel_payload = self._sentinel_payload(
                identity=identity,
                workspace_path=active_workspace,
                template_sha256=template_sha256,
                created_at=created_at,
            )
            _atomic_write_json(temporary / SENTINEL_FILENAME, sentinel_payload)
            self._validate_preflight_tree(
                temporary,
                identity=identity,
                template_entries=entries,
                expected_template_sha256=template_sha256,
                expected_workspace=active_workspace,
            )
            os.replace(temporary, active_workspace)
            temporary = None
            self._write_lock_identity(lock_handle, identity)
            return self._build_lease(
                identity=identity,
                active_workspace=active_workspace,
                lock_path=lock_path,
                lock_handle=lock_handle,
                template_sha256=template_sha256,
                created_at=created_at,
            )
        except WorkspaceIsolationError:
            self._release_lock_handle(lock_handle)
            raise
        except Exception as exc:
            self._release_lock_handle(lock_handle)
            raise WorkspaceIsolationError(
                "workspace_prepare_failed",
                f"Unable to prepare benchmark attempt workspace: {exc}",
                details={"workspace_path": str(active_workspace), "exception_type": type(exc).__name__},
            ) from exc
        finally:
            if temporary is not None and temporary.exists():
                shutil.rmtree(temporary)

    def prepare_set(self, identities: tuple[AttemptIdentity, ...] | list[AttemptIdentity]) -> WorkspaceLeaseSet:
        prepared: list[AttemptWorkspaceLease] = []
        quarantined: list[str] = []
        try:
            for identity in identities:
                prepared.append(self.prepare(identity))
        except WorkspaceIsolationError as exc:
            for lease in reversed(prepared):
                try:
                    quarantined.append(str(self.quarantine(lease, reason="lease_set_prepare_failed")))
                except WorkspaceIsolationError as quarantine_error:
                    quarantined.append(f"ERROR:{quarantine_error.message}")
            exc.details["prepared_lease_quarantine"] = quarantined
            raise
        return WorkspaceLeaseSet(leases=tuple(prepared))

    def quarantine(self, lease: AttemptWorkspaceLease, *, reason: str) -> Path:
        if lease.released:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                "Cannot quarantine a released benchmark workspace lease.",
                details={"workspace_path": str(lease.active_workspace)},
            )
        try:
            self._read_and_validate_sentinel(
                lease.active_workspace,
                expected_identity=lease.identity,
                expected_template_sha256=lease.template_sha256,
            )
            return self._quarantine_managed_path(lease.active_workspace, lease.identity, reason=reason)
        except WorkspaceIsolationError:
            raise
        except Exception as exc:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                f"Unable to quarantine managed benchmark workspace: {exc}",
                details={"workspace_path": str(lease.active_workspace), "exception_type": type(exc).__name__},
            ) from exc
        finally:
            self._release_lease(lease)

    def cleanup_boundary_writes(self, audit: _WorkspaceAudit) -> dict[str, Any]:
        report: dict[str, Any] = {
            "attempted_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "items": [],
        }
        for finding in audit.findings:
            if finding.get("access_mode") not in {"write", "mutate"}:
                continue
            if finding.get("operation_outcome") != "succeeded":
                continue
            if finding.get("resource_provenance") != "current_attempt_owned":
                continue
            report["attempted_count"] += 1
            item = {
                "resolved_path": str(finding.get("resolved_path") or ""),
                "status": "failed",
            }
            try:
                target = Path(item["resolved_path"]).expanduser().resolve(strict=False)
                matched_root = Path(str(finding.get("matched_root") or "")).expanduser().resolve(strict=False)
                if target == matched_root or not _path_is_within(target, matched_root):
                    raise ValueError("cleanup target is not a strict child of the matched protected root")
                sentinel = target / SENTINEL_FILENAME if target.is_dir() else None
                if sentinel is not None and sentinel.exists():
                    raise ValueError("cleanup target contains a managed workspace sentinel")
                if target.is_symlink():
                    target.unlink()
                elif target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                item["status"] = "succeeded"
                report["succeeded_count"] += 1
            except Exception as exc:
                item["exception_type"] = type(exc).__name__
                item["reason"] = _redact_text(str(exc), limit=240)
                report["failed_count"] += 1
            report["items"].append(item)
        return report

    def seal(self, lease: AttemptWorkspaceLease, outcome: AttemptOutcome) -> WorkspaceArchive:
        if lease.archive is not None:
            return lease.archive
        if lease.released:
            raise WorkspaceIsolationError(
                "workspace_archive_failed",
                "Cannot seal a released benchmark workspace lease.",
                details={"workspace_path": str(lease.active_workspace)},
            )
        self._validate_identity_scope(lease.identity, allow_previous_invocation=True)
        final_archive = self._archive_path(lease.identity)
        temporary_archive = final_archive.parent / f".{final_archive.name}.seal-{uuid.uuid4().hex}"
        managed_location: Path | None = lease.active_workspace
        try:
            if final_archive.exists() or final_archive.is_symlink():
                raise WorkspaceIsolationError(
                    "workspace_archive_failed",
                    "Benchmark workspace archive path already exists; refusing to overwrite it.",
                    details={"archive_path": str(final_archive)},
                )
            sentinel = self._read_and_validate_sentinel(
                lease.active_workspace,
                expected_identity=lease.identity,
                expected_template_sha256=lease.template_sha256,
            )
            self._validate_runtime_tree(lease.active_workspace)
            final_archive.parent.mkdir(parents=True, exist_ok=True)
            temporary_archive.mkdir(mode=0o700)
            temporary_workspace = temporary_archive / "workspace"
            sentinel_sha256 = _sha256_file(lease.sentinel_path)
            if lease.active_workspace.stat().st_dev == final_archive.parent.stat().st_dev:
                os.replace(lease.active_workspace, temporary_workspace)
                managed_location = temporary_archive
            else:
                shutil.copytree(lease.active_workspace, temporary_workspace, symlinks=False)
                self._validate_copied_workspace(lease.active_workspace, temporary_workspace, sentinel_sha256)
                managed_location = lease.active_workspace
            file_count, total_bytes = _tree_stats(temporary_workspace)
            sealed_at = _utc_now()
            manifest_payload = {
                "kind": ARCHIVE_KIND,
                "schema_version": SCHEMA_VERSION,
                **lease.identity.sentinel_fields(),
                "runner_status": outcome.runner_status,
                "archive_reason": outcome.archive_reason,
                "started_at": str(sentinel["created_at"]),
                "sealed_at": sealed_at,
                "source_workspace": str(lease.active_workspace),
                "archive_workspace": str(final_archive / "workspace"),
                "file_count": file_count,
                "total_bytes": total_bytes,
                "sentinel_sha256": sentinel_sha256,
                "workspace_isolation": _ensure_workspace_audit(outcome.contamination_audit).to_payload(),
                "scratch_contract_version": SCRATCH_CONTRACT_VERSION,
            }
            temporary_manifest = temporary_archive / "workspace-archive-manifest.json"
            _atomic_write_json(temporary_manifest, manifest_payload)
            os.replace(temporary_archive, final_archive)
            managed_location = lease.active_workspace if lease.active_workspace.exists() else None
            if lease.active_workspace.exists():
                shutil.rmtree(lease.active_workspace)
                managed_location = None
            manifest_path = final_archive / "workspace-archive-manifest.json"
            archive = WorkspaceArchive(
                workspace=final_archive / "workspace",
                manifest_path=manifest_path,
                manifest_sha256=_sha256_file(manifest_path),
                payload=manifest_payload,
            )
            lease.archive = archive
            return archive
        except WorkspaceIsolationError:
            if managed_location is not None and managed_location.exists():
                self._quarantine_managed_path(managed_location, lease.identity, reason="archive_failed")
            raise
        except Exception as exc:
            quarantine_path = None
            if managed_location is not None and managed_location.exists():
                quarantine_path = self._quarantine_managed_path(
                    managed_location,
                    lease.identity,
                    reason="archive_failed",
                )
            raise WorkspaceIsolationError(
                "workspace_archive_failed",
                f"Unable to archive benchmark attempt workspace: {exc}",
                details={
                    "workspace_path": str(lease.active_workspace),
                    "archive_path": str(final_archive),
                    "quarantine_path": str(quarantine_path or ""),
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        finally:
            if temporary_archive.exists():
                shutil.rmtree(temporary_archive)
            self._release_lease(lease)

    def recover_incomplete(self, invocation_id: str) -> list[WorkspaceArchive]:
        invocation_root = self.run_runtime_root / workspace_slug(invocation_id)
        active_root = invocation_root / "active"
        if not active_root.exists():
            return []
        if active_root.is_symlink():
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                "Previous invocation active root is a symlink.",
                details={"active_root": str(active_root)},
            )
        archives: list[WorkspaceArchive] = []
        candidates = self._active_workspace_candidates(active_root)
        for workspace in sorted(candidates):
            if workspace.is_symlink():
                raise WorkspaceIsolationError(
                    "workspace_recovery_failed",
                    "Previous invocation contains a symlink workspace.",
                    details={"workspace_path": str(workspace)},
                )
            try:
                sentinel = self._read_sentinel_payload(workspace)
                identity = self._identity_from_sentinel(sentinel)
                if identity.run_id != self.run_id or identity.invocation_id != invocation_id:
                    raise ValueError("sentinel scope does not match recovery target")
                recorded_workspace = Path(str(sentinel.get("workspace_path") or "")).expanduser().resolve()
                workspace_is_prepare_temp = (
                    workspace.parent == recorded_workspace.parent
                    and workspace.name.startswith(f".{recorded_workspace.name}.prepare-")
                )
                if workspace.resolve() != recorded_workspace and not workspace_is_prepare_temp:
                    raise ValueError("sentinel workspace path mismatch")
                template_sha256 = str(sentinel.get("template_sha256") or "")
                if not re.fullmatch(r"[0-9a-f]{64}", template_sha256):
                    raise ValueError("invalid template hash")
            except Exception as exc:
                raise WorkspaceIsolationError(
                    "workspace_recovery_failed",
                    "Unable to prove ownership of an incomplete benchmark workspace; it was left unchanged.",
                    details={"workspace_path": str(workspace), "exception_type": type(exc).__name__},
                ) from exc
            lock_path = self.lock_path(
                group_id=identity.group_id,
                agent_id=identity.agent_id,
                invocation_id=identity.invocation_id,
            )
            lock_handle = self._acquire_lock(lock_path, identity)
            if workspace_is_prepare_temp:
                try:
                    self._quarantine_managed_path(workspace, identity, reason="preparing_crash_recovery")
                finally:
                    self._release_lock_handle(lock_handle)
                continue
            lease = self._build_lease(
                identity=identity,
                active_workspace=workspace,
                lock_path=lock_path,
                lock_handle=lock_handle,
                template_sha256=template_sha256,
                created_at=str(sentinel.get("created_at") or ""),
            )
            archives.append(
                self.seal(
                    lease,
                    AttemptOutcome(runner_status="aborted", archive_reason="shutdown_recovery"),
                )
            )
        return archives

    def recover_all_incomplete(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "status": "clean",
            "recovered_invocations": [],
            "archives": [],
            "quarantines": [],
        }
        if self.run_runtime_root.exists():
            current_root = self.invocation_runtime_root.resolve(strict=False)
            for invocation_root in sorted(path for path in self.run_runtime_root.iterdir() if path.is_dir()):
                if invocation_root.resolve(strict=False) == current_root:
                    continue
                active_root = invocation_root / "active"
                if not active_root.exists():
                    continue
                invocation_ids: set[str] = set()
                candidates = self._active_workspace_candidates(active_root)
                if not candidates:
                    continue
                for workspace in candidates:
                    try:
                        sentinel = self._read_sentinel_payload(workspace)
                        identity = self._identity_from_sentinel(sentinel)
                    except Exception as exc:
                        raise WorkspaceIsolationError(
                            "workspace_recovery_failed",
                            "Startup recovery found an unknown active workspace and left it unchanged.",
                            details={"workspace_path": str(workspace), "exception_type": type(exc).__name__},
                        ) from exc
                    invocation_ids.add(identity.invocation_id)
                for previous_invocation_id in sorted(invocation_ids):
                    quarantines_before = set(self.quarantine_root.iterdir()) if self.quarantine_root.exists() else set()
                    archives = self.recover_incomplete(previous_invocation_id)
                    report["recovered_invocations"].append(previous_invocation_id)
                    report["archives"].extend(str(archive.manifest_path) for archive in archives)
                    quarantines_after = set(self.quarantine_root.iterdir()) if self.quarantine_root.exists() else set()
                    report["quarantines"].extend(str(path) for path in sorted(quarantines_after - quarantines_before))

        if self.archive_root.exists():
            for temporary_archive in sorted(
                path for path in self.archive_root.rglob(".*.seal-*") if path.is_dir()
            ):
                workspace = temporary_archive / "workspace"
                try:
                    sentinel = self._read_sentinel_payload(workspace)
                    identity = self._identity_from_sentinel(sentinel)
                    if identity.run_id != self.run_id:
                        raise ValueError("sealing temp belongs to another run")
                    expected_source = self.active_workspace_path(
                        group_id=identity.group_id,
                        agent_id=identity.agent_id,
                        invocation_id=identity.invocation_id,
                    )
                    if str(sentinel.get("workspace_path") or "") != str(expected_source.resolve(strict=False)):
                        raise ValueError("sealing temp sentinel source path mismatch")
                except Exception as exc:
                    raise WorkspaceIsolationError(
                        "workspace_recovery_failed",
                        "Startup recovery found an unowned sealing temp and left it unchanged.",
                        details={"path": str(temporary_archive), "exception_type": type(exc).__name__},
                    ) from exc
                quarantine = self._quarantine_managed_path(
                    temporary_archive,
                    identity,
                    reason="sealing_crash_recovery",
                )
                report["quarantines"].append(str(quarantine))

        if report["archives"] or report["quarantines"]:
            report["status"] = "recovered"
        return report

    @staticmethod
    def _active_workspace_candidates(active_root: Path) -> list[Path]:
        candidates: list[Path] = []
        for group_root in active_root.iterdir():
            if not group_root.is_dir() or group_root.is_symlink():
                continue
            candidates.extend(
                path for path in group_root.iterdir() if path.is_dir() or path.is_symlink()
            )
        return sorted(candidates)

    def _retry_audit_from_archive(
        self,
        *,
        lease: AttemptWorkspaceLease,
        runner_meta: Mapping[str, Any],
        current_transcript: Path,
        recovery_candidates: tuple[str, ...],
        allowed_roots: tuple[Path, ...] | list[Path],
        environment: Mapping[str, str] | None,
        policy: _WorkspaceAccessPolicy,
    ) -> _WorkspaceAudit | None:
        if runner_meta.get("workspace_audit_recovery_attempted") is True:
            return None
        for candidate_text in recovery_candidates:
            candidate = Path(candidate_text).expanduser()
            if candidate == current_transcript or candidate.is_symlink() or not candidate.is_file():
                continue
            recovered_meta = dict(runner_meta)
            session = recovered_meta.get("session_isolation")
            session = dict(session) if isinstance(session, Mapping) else {}
            session["postflight_entry_session_file"] = str(candidate)
            for key in (
                "archived_session_file",
                "archive_transcript_path",
                "archived_transcript_path",
                "transcript_archive_path",
            ):
                session.pop(key, None)
                recovered_meta.pop(key, None)
            recovered_meta["session_isolation"] = session
            recovered_meta["workspace_audit_recovery_attempted"] = True
            recovered = self.audit_attempt(
                lease,
                recovered_meta,
                allowed_roots=allowed_roots,
                environment=environment,
                policy=policy,
            )
            return _WorkspaceAudit(
                audit_execution_status=recovered.audit_execution_status,
                boundary_status=recovered.boundary_status,
                contamination_status=recovered.contamination_status,
                adjudication=recovered.adjudication,
                findings=recovered.findings,
                recovery={
                    "attempted": True,
                    "succeeded": recovered.audit_execution_status == "complete",
                    "source": "archive",
                    "transcript_path": _redact_text(str(candidate)),
                    "model_reinvoked": False,
                },
            )
        return None

    def audit_attempt(
        self,
        lease: AttemptWorkspaceLease,
        runner_meta: Mapping[str, Any],
        *,
        allowed_roots: tuple[Path, ...] | list[Path] = (),
        environment: Mapping[str, str] | None = None,
        policy: _WorkspaceAccessPolicy | None = None,
    ) -> _WorkspaceAudit:
        session_isolation = runner_meta.get("session_isolation")
        session_isolation = session_isolation if isinstance(session_isolation, Mapping) else {}
        requested_path = str(session_isolation.get("postflight_entry_session_file") or "").strip()
        recovery_candidates = _audit_recovery_candidates(runner_meta)
        transcript_path, recovery = _select_audit_transcript(requested_path, recovery_candidates)
        if transcript_path is None:
            finding = {
                "rule_id": "transcript_unavailable",
                "tool_call_id": "",
                "tool_name": "",
                "candidate_source": "session_isolation.postflight_entry_session_file",
                "access_mode": "unknown",
                "operation_outcome": "unknown",
                "resource_provenance": "unknown",
                "information_exposure": "unknown",
                "boundary_effect": "unknown",
                "command_excerpt": _redact_text(requested_path),
                "evidence": {},
            }
            return _adjudicate_workspace_findings(
                (finding,),
                audit_execution_status="unavailable",
                recovery=recovery,
            )

        active_policy = policy or self.policy_for_lease(
            lease,
            role=lease.identity.runner_kind,
            skills_enabled=bool(allowed_roots),
            read_scopes=allowed_roots,
        )
        try:
            transcript_lines = transcript_path.read_text(encoding="utf-8").splitlines()
            payloads: list[tuple[int, Any]] = []
            for line_number, raw_line in enumerate(transcript_lines, start=1):
                if not raw_line.strip():
                    continue
                payloads.append((line_number, json.loads(raw_line)))
            events, standalone_results = _tool_events_from_transcript(payloads)
        except Exception as exc:
            recovered = self._retry_audit_from_archive(
                lease=lease,
                runner_meta=runner_meta,
                current_transcript=transcript_path,
                recovery_candidates=recovery_candidates,
                allowed_roots=allowed_roots,
                environment=environment,
                policy=active_policy,
            )
            if recovered is not None:
                return recovered
            finding = _transcript_audit_failure(exc)
            finding.update(
                {
                    "tool_call_id": "",
                    "candidate_source": "transcript",
                    "access_mode": "unknown",
                    "operation_outcome": "unknown",
                    "resource_provenance": "unknown",
                    "information_exposure": "unknown",
                    "boundary_effect": "unknown",
                    "evidence": {},
                }
            )
            return _adjudicate_workspace_findings(
                (finding,),
                audit_execution_status="unavailable",
                recovery=recovery,
            )

        findings: list[dict[str, Any]] = []
        try:
            for event in events:
                try:
                    findings.extend(
                        _forbidden_access_findings(
                            event=event,
                            workspace=lease.active_workspace,
                            policy=active_policy,
                            environment=environment or {},
                        )
                    )
                except Exception as exc:
                    recovered = self._retry_audit_from_archive(
                        lease=lease,
                        runner_meta=runner_meta,
                        current_transcript=transcript_path,
                        recovery_candidates=recovery_candidates,
                        allowed_roots=allowed_roots,
                        environment=environment,
                        policy=active_policy,
                    )
                    if recovered is not None:
                        return recovered
                    finding = _transcript_audit_failure(
                        exc,
                        line_number=event.call_line,
                        tool_name=event.tool_name,
                        arguments=event.arguments,
                    )
                    finding.update(
                        {
                            "tool_call_id": event.tool_call_id,
                            "candidate_source": "transcript.tool_call",
                            "access_mode": "unknown",
                            "operation_outcome": _operation_outcome(event.result),
                            "resource_provenance": "unknown",
                            "information_exposure": "unknown",
                            "boundary_effect": "unknown",
                            "evidence": {
                                "call_line": event.call_line,
                                "result_line": event.result_line,
                            },
                        }
                    )
                    return _adjudicate_workspace_findings(
                        (finding,),
                        audit_execution_status="unavailable",
                        recovery=recovery,
                    )
            for result_line, result_message in standalone_results:
                fallback = _workdir_fallback_finding(
                    result_message,
                    line_number=result_line,
                    policy=active_policy,
                )
                if fallback is not None:
                    findings.append(fallback)
            for event in events:
                if event.result is None:
                    continue
                fallback = _workdir_fallback_finding(
                    event.result,
                    line_number=event.result_line,
                    policy=active_policy,
                    tool_call_id=event.tool_call_id,
                    operation_outcome=_operation_outcome(event.result),
                    call_line=event.call_line,
                )
                if fallback is not None:
                    findings.append(fallback)
        except Exception as exc:
            finding = _transcript_audit_failure(exc)
            finding.update(
                {
                    "tool_call_id": "",
                    "candidate_source": "transcript",
                    "access_mode": "unknown",
                    "operation_outcome": "unknown",
                    "resource_provenance": "unknown",
                    "information_exposure": "unknown",
                    "boundary_effect": "unknown",
                    "evidence": {},
                }
            )
            return _adjudicate_workspace_findings(
                (finding,),
                audit_execution_status="unavailable",
                recovery=recovery,
            )
        return _adjudicate_workspace_findings(findings, recovery=recovery)

    def _validate_identity_scope(self, identity: AttemptIdentity, *, allow_previous_invocation: bool = False) -> None:
        if identity.run_id != self.run_id or (
            not allow_previous_invocation and identity.invocation_id != self.invocation_id
        ):
            raise WorkspaceIsolationError(
                "workspace_path_unsafe",
                "Attempt identity does not belong to this workspace manager scope.",
                details={"run_id": identity.run_id, "invocation_id": identity.invocation_id},
            )

    def _ensure_runtime_layout(self, *, active_workspace: Path, lock_path: Path) -> None:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)
        active_workspace.parent.mkdir(parents=True, exist_ok=True)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        for path, root in ((active_workspace, self.runtime_root), (lock_path, self.runtime_root)):
            self._ensure_contained(path, root)
        for parent in (active_workspace.parent, lock_path.parent):
            if parent.is_symlink():
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Benchmark runtime path contains a symlink.",
                    details={"path": str(parent)},
                )

    @staticmethod
    def _ensure_contained(path: Path, root: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(root.resolve())
        except ValueError as exc:
            raise WorkspaceIsolationError(
                "workspace_path_unsafe",
                "Benchmark workspace path escapes its managed root.",
                details={"path": str(path), "root": str(root)},
            ) from exc

    def _template_entries(self, template: WorkspaceTemplate) -> tuple[dict[PurePosixPath, bytes], str]:
        entries: dict[PurePosixPath, bytes] = {}
        try:
            if template.template_id not in self.templates or self.templates[template.template_id] != template:
                raise ValueError("template is not registered")
            if template.source_dir is not None:
                root = template.source_dir.expanduser().resolve()
                if not root.is_dir():
                    raise ValueError(f"template directory does not exist: {root}")
                for source in sorted(root.rglob("*")):
                    relative = PurePosixPath(source.relative_to(root).as_posix())
                    self._validate_template_relative_path(relative)
                    mode = source.lstat().st_mode
                    if stat.S_ISDIR(mode):
                        continue
                    if not stat.S_ISREG(mode):
                        raise ValueError(f"template contains non-regular path: {relative}")
                    entries[relative] = source.read_bytes()
            for relative_text, source in sorted(template.files.items()):
                relative = PurePosixPath(relative_text)
                self._validate_template_relative_path(relative)
                mode = source.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(f"template source is not a regular file: {source}")
                if relative in entries:
                    raise ValueError(f"duplicate template path: {relative}")
                entries[relative] = source.read_bytes()
            if template.agents_base is not None or template.agents_overlay is not None:
                if template.agents_base is None or template.agents_overlay is None:
                    raise ValueError("template agent contract requires both base and overlay")
                base = template.agents_base.expanduser().resolve()
                overlay = template.agents_overlay.expanduser().resolve()
                if not base.is_file() or not overlay.is_file():
                    raise ValueError("template agent contract source is missing")
                final_contract = base.read_text(encoding="utf-8").rstrip() + "\n\n" + overlay.read_text(
                    encoding="utf-8"
                ).strip() + "\n"
                agents_path = PurePosixPath("AGENTS.md")
                if agents_path in entries:
                    raise ValueError("duplicate template path: AGENTS.md")
                entries[agents_path] = final_contract.encode("utf-8")
            if not entries:
                raise ValueError("template contains no files")
        except WorkspaceIsolationError:
            raise
        except Exception as exc:
            raise WorkspaceIsolationError(
                "workspace_template_invalid",
                f"Invalid benchmark workspace template `{template.template_id}`: {exc}",
                details={"template_id": template.template_id, "exception_type": type(exc).__name__},
            ) from exc
        digest = hashlib.sha256()
        for relative, content in sorted(entries.items(), key=lambda item: item[0].as_posix()):
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0file\0")
            digest.update(content)
            digest.update(b"\0")
        return entries, digest.hexdigest()

    @staticmethod
    def _validate_template_relative_path(relative: PurePosixPath) -> None:
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"unsafe template path: {relative}")
        if ".git" in relative.parts:
            raise ValueError(f"template contains .git: {relative}")
        if relative.parts[0] in {SENTINEL_FILENAME, "scratch", ".benchmark-scratch"}:
            raise ValueError(f"template uses manager-owned path: {relative}")

    @staticmethod
    def _materialize_template(root: Path, entries: Mapping[PurePosixPath, bytes]) -> None:
        for relative, content in entries.items():
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(content)

    def _validate_preflight_tree(
        self,
        root: Path,
        *,
        identity: AttemptIdentity,
        template_entries: Mapping[PurePosixPath, bytes],
        expected_template_sha256: str,
        expected_workspace: Path,
    ) -> None:
        allowed_files = {relative.as_posix() for relative in template_entries}
        allowed_files.add(SENTINEL_FILENAME)
        scratch_relative = PurePosixPath("scratch")
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            relative_posix = relative.as_posix()
            mode = path.lstat().st_mode
            if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Benchmark workspace preflight found a symlink or special file.",
                    details={"path": str(path)},
                )
            if ".git" in relative.parts:
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Benchmark workspace preflight found forbidden Git metadata.",
                    details={"path": str(path)},
                )
            under_scratch = relative_posix == scratch_relative.as_posix() or relative_posix.startswith(
                scratch_relative.as_posix() + "/"
            )
            if stat.S_ISREG(mode) and relative_posix not in allowed_files and not under_scratch:
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Benchmark workspace preflight found an unexpected file.",
                    details={"path": str(path)},
                )
        sentinel = self._read_and_validate_sentinel(
            root,
            expected_identity=identity,
            expected_template_sha256=expected_template_sha256,
            expected_workspace=expected_workspace,
        )
        if sentinel["template_sha256"] != expected_template_sha256:
            raise WorkspaceIsolationError("workspace_template_invalid", "Materialized template hash does not match sentinel.")
        materialized = {
            PurePosixPath(relative): (root / relative).read_bytes()
            for relative in sorted(allowed_files - {SENTINEL_FILENAME})
        }
        digest = hashlib.sha256()
        for relative, content in sorted(materialized.items(), key=lambda item: item[0].as_posix()):
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0file\0")
            digest.update(content)
            digest.update(b"\0")
        if digest.hexdigest() != expected_template_sha256:
            raise WorkspaceIsolationError(
                "workspace_template_invalid",
                "Materialized benchmark workspace template content does not match its hash.",
            )

    def _resume_or_reject_existing(
        self,
        *,
        identity: AttemptIdentity,
        active_workspace: Path,
        lock_path: Path,
        lock_handle: BinaryIO,
        template_sha256: str,
        template_entries: Mapping[PurePosixPath, bytes],
    ) -> AttemptWorkspaceLease:
        try:
            sentinel = self._read_and_validate_sentinel(
                active_workspace,
                expected_identity=identity,
                expected_template_sha256=template_sha256,
            )
        except WorkspaceIsolationError as expected_error:
            try:
                existing = self._read_sentinel_payload(active_workspace)
                existing_identity = self._identity_from_sentinel(existing)
                if str(existing.get("workspace_path") or "") != str(active_workspace.resolve()):
                    raise ValueError("existing sentinel workspace path mismatch")
                existing_hash = str(existing.get("template_sha256") or "")
                if not re.fullmatch(r"[0-9a-f]{64}", existing_hash):
                    raise ValueError("existing sentinel template hash invalid")
            except Exception:
                raise expected_error
            quarantine = self._quarantine_managed_path(
                active_workspace,
                existing_identity,
                reason="incomplete_attempt_conflict",
            )
            raise WorkspaceIsolationError(
                "workspace_sentinel_invalid",
                "Active workspace belonged to another incomplete managed attempt and was quarantined; current attempt failed closed.",
                details={
                    "workspace_path": str(active_workspace),
                    "quarantine_path": str(quarantine),
                    "existing_identity": existing_identity.sentinel_fields(),
                },
            ) from expected_error
        self._validate_runtime_tree(active_workspace)
        self._write_lock_identity(lock_handle, identity)
        return self._build_lease(
            identity=identity,
            active_workspace=active_workspace,
            lock_path=lock_path,
            lock_handle=lock_handle,
            template_sha256=template_sha256,
            created_at=str(sentinel["created_at"]),
        )

    @staticmethod
    def _validate_runtime_tree(root: Path) -> None:
        for path in root.rglob("*"):
            mode = path.lstat().st_mode
            if path.is_symlink() or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or ".git" in path.relative_to(root).parts:
                raise WorkspaceIsolationError(
                    "workspace_path_unsafe",
                    "Existing managed workspace contains a symlink, special file, or Git metadata.",
                    details={"path": str(path)},
                )

    def _sentinel_payload(
        self,
        *,
        identity: AttemptIdentity,
        workspace_path: Path,
        template_sha256: str,
        created_at: str,
    ) -> dict[str, Any]:
        return {
            "kind": SENTINEL_KIND,
            "schema_version": SCHEMA_VERSION,
            **identity.sentinel_fields(),
            "workspace_path": str(workspace_path.resolve(strict=False)),
            "template_sha256": template_sha256,
            "created_at": created_at,
        }

    @staticmethod
    def _read_sentinel_payload(workspace: Path) -> dict[str, Any]:
        sentinel_path = workspace / SENTINEL_FILENAME
        if sentinel_path.is_symlink() or not sentinel_path.is_file():
            raise ValueError("sentinel is missing or not a regular file")
        payload = json.loads(sentinel_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("sentinel is not a JSON object")
        return payload

    def _read_and_validate_sentinel(
        self,
        workspace: Path,
        *,
        expected_identity: AttemptIdentity,
        expected_template_sha256: str,
        expected_workspace: Path | None = None,
    ) -> dict[str, Any]:
        try:
            payload = self._read_sentinel_payload(workspace)
            if payload.get("kind") != SENTINEL_KIND or payload.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("sentinel kind or schema version mismatch")
            for name, value in expected_identity.sentinel_fields().items():
                if payload.get(name) != value:
                    raise ValueError(f"sentinel identity mismatch at {name}")
            target = expected_workspace or workspace
            if str(payload.get("workspace_path") or "") != str(target.resolve(strict=False)):
                raise ValueError("sentinel workspace path mismatch")
            if payload.get("template_sha256") != expected_template_sha256:
                raise ValueError("sentinel template hash mismatch")
            if not str(payload.get("created_at") or ""):
                raise ValueError("sentinel created_at is missing")
            return payload
        except WorkspaceIsolationError:
            raise
        except Exception as exc:
            raise WorkspaceIsolationError(
                "workspace_sentinel_invalid",
                f"Benchmark workspace sentinel is missing, damaged, or does not match the attempt: {exc}",
                details={"workspace_path": str(workspace), "exception_type": type(exc).__name__},
            ) from exc

    @staticmethod
    def _identity_from_sentinel(payload: Mapping[str, Any]) -> AttemptIdentity:
        if payload.get("kind") != SENTINEL_KIND or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("sentinel kind or schema version mismatch")
        return AttemptIdentity(
            run_id=str(payload["run_id"]),
            invocation_id=str(payload["invocation_id"]),
            group_id=str(payload["group_id"]),
            runner_kind=str(payload["runner_kind"]),
            agent_id=str(payload["agent_id"]),
            record_id=str(payload["record_id"]),
            attempt_index=int(payload["attempt_index"]),
            session_id=str(payload["session_id"]),
            template_id=str(payload["template_id"]),
        )

    def _acquire_lock(self, lock_path: Path, identity: AttemptIdentity) -> BinaryIO:
        if lock_path.is_symlink():
            raise WorkspaceIsolationError(
                "workspace_path_unsafe",
                "Benchmark workspace lock path is a symlink.",
                details={"lock_path": str(lock_path)},
            )
        handle = lock_path.open("a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._write_lock_identity(handle, identity)
            return handle
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise WorkspaceIsolationError(
                    "workspace_lock_conflict",
                    "Benchmark workspace is already leased by another attempt.",
                    details={"lock_path": str(lock_path)},
                ) from exc
            raise

    @staticmethod
    def _write_lock_identity(handle: BinaryIO, identity: AttemptIdentity) -> None:
        payload = json.dumps(identity.sentinel_fields(), sort_keys=True).encode("utf-8") + b"\n"
        handle.seek(0)
        handle.truncate()
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())

    @staticmethod
    def _release_lock_handle(handle: BinaryIO) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _release_lease(self, lease: AttemptWorkspaceLease) -> None:
        if lease.released:
            return
        self._release_lock_handle(lease._lock_handle)
        lease.released = True

    @staticmethod
    def _scratch_dir(root: Path, identity: AttemptIdentity) -> Path:
        return root / "scratch"

    def _build_lease(
        self,
        *,
        identity: AttemptIdentity,
        active_workspace: Path,
        lock_path: Path,
        lock_handle: BinaryIO,
        template_sha256: str,
        created_at: str,
    ) -> AttemptWorkspaceLease:
        scratch_dir = self._scratch_dir(active_workspace, identity)
        return AttemptWorkspaceLease(
            identity=identity,
            active_workspace=active_workspace,
            scratch_dir=scratch_dir,
            request_dir=scratch_dir / "requests",
            output_dir=scratch_dir / "outputs",
            notes_dir=scratch_dir / "notes",
            tmp_dir=scratch_dir / "tmp",
            sentinel_path=active_workspace / SENTINEL_FILENAME,
            lock_path=lock_path,
            template_sha256=template_sha256,
            created_at=created_at,
            _lock_handle=lock_handle,
        )

    def _archive_path(self, identity: AttemptIdentity) -> Path:
        return (
            self.archive_root
            / workspace_slug(identity.group_id)
            / workspace_slug(identity.record_id, limit=80)
            / f"attempt-{identity.attempt_index}-{workspace_slug(identity.session_id, limit=96)}"
        )

    @staticmethod
    def _validate_copied_workspace(source: Path, copied: Path, sentinel_sha256: str) -> None:
        if _tree_stats(source) != _tree_stats(copied):
            raise RuntimeError("cross-filesystem workspace copy size/count mismatch")
        copied_sentinel = copied / SENTINEL_FILENAME
        if _sha256_file(copied_sentinel) != sentinel_sha256:
            raise RuntimeError("cross-filesystem workspace sentinel hash mismatch")

    def _quarantine_managed_path(
        self,
        path: Path,
        identity: AttemptIdentity,
        *,
        reason: str,
    ) -> Path:
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        name = "-".join(
            [
                workspace_slug(identity.group_id, limit=40),
                workspace_slug(identity.record_id, limit=40),
                f"attempt-{identity.attempt_index}",
                reason,
                uuid.uuid4().hex[:8],
            ]
        )
        destination = self.quarantine_root / name
        shutil.move(str(path), str(destination))
        return destination


def default_workspace_templates(project_root: Path) -> dict[str, WorkspaceTemplate]:
    project_root = project_root.expanduser().resolve()
    resource_root = project_root / "benchmarking" / "resources" / "agent-workspace-templates"
    base_contract = resource_root / "common" / "AGENTS.base.md"
    return {
        "single-llm-skills-on-v1": WorkspaceTemplate(
            template_id="single-llm-skills-on-v1",
            files={"TOOLS.md": resource_root / "single-llm-skills-on" / "TOOLS.md"},
            agents_base=base_contract,
            agents_overlay=resource_root / "single-llm-skills-on" / "AGENTS.overlay.md",
        ),
        "single-llm-skills-off-v1": WorkspaceTemplate(
            template_id="single-llm-skills-off-v1",
            agents_base=base_contract,
            agents_overlay=resource_root / "single-llm-skills-off" / "AGENTS.overlay.md",
        ),
        "judge-v1": WorkspaceTemplate(
            template_id="judge-v1",
            agents_base=base_contract,
            agents_overlay=resource_root / "judge" / "AGENTS.overlay.md",
        ),
        "chemqa-role-v1": WorkspaceTemplate(
            template_id="chemqa-role-v1",
            agents_base=base_contract,
            agents_overlay=resource_root / "chemqa-role" / "AGENTS.overlay.md",
        ),
    }
