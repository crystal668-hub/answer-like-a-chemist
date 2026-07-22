from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


WORKSPACE_POLICY_SCHEMA_VERSION = 1
WORKSPACE_ISOLATION_SCHEMA_VERSION = 3


@dataclass(frozen=True)
class ProtectedRoot:
    policy_id: str
    path: Path
    source: str


@dataclass(frozen=True)
class AccessScope:
    scope_id: str
    path: Path
    kind: str
    source: str


@dataclass(frozen=True)
class WorkspaceAccessPolicy:
    schema_version: int
    role: str
    skills_enabled: bool
    read_scopes: tuple[AccessScope, ...]
    write_scopes: tuple[AccessScope, ...]
    exec_workdir_scopes: tuple[AccessScope, ...]
    protected_roots: tuple[ProtectedRoot, ...]

    def __post_init__(self) -> None:
        if int(self.schema_version) != WORKSPACE_POLICY_SCHEMA_VERSION:
            raise ValueError(f"Unsupported workspace policy schema version: {self.schema_version}")
        if not str(self.role or "").strip():
            raise ValueError("Workspace access policy role must be non-empty.")
        object.__setattr__(self, "role", str(self.role).strip())
        object.__setattr__(self, "read_scopes", _normalize_access_scopes(self.read_scopes))
        object.__setattr__(self, "write_scopes", _normalize_access_scopes(self.write_scopes))
        object.__setattr__(self, "exec_workdir_scopes", _normalize_access_scopes(self.exec_workdir_scopes))
        object.__setattr__(self, "protected_roots", _normalize_protected_roots(self.protected_roots))
        for write_scope in self.write_scopes:
            if not any(
                _access_scope_matches(write_scope.path, read_scope)
                for read_scope in self.read_scopes
                if read_scope.kind == "directory"
            ):
                raise ValueError(
                    f"Workspace write scope `{write_scope.scope_id}` must be contained by a read directory scope."
                )

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.to_payload(include_digest=False), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def allows(self, access_mode: str, candidate: Path) -> bool:
        mode = str(access_mode or "").strip().lower()
        if mode in {"write", "mutate"}:
            scopes = self.write_scopes
        elif mode == "workdir":
            scopes = self.exec_workdir_scopes
        else:
            scopes = self.read_scopes
        resolved = Path(candidate).expanduser().resolve(strict=False)
        return any(_access_scope_matches(resolved, scope) for scope in scopes)

    def to_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        def scope_payload(scope: AccessScope) -> dict[str, str]:
            return {
                "scope_id": scope.scope_id,
                "path": str(scope.path),
                "kind": scope.kind,
                "source": scope.source,
            }

        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "role": self.role,
            "skills_enabled": self.skills_enabled,
            "read_scopes": [scope_payload(scope) for scope in self.read_scopes],
            "write_scopes": [scope_payload(scope) for scope in self.write_scopes],
            "exec_workdir_scopes": [scope_payload(scope) for scope in self.exec_workdir_scopes],
            "protected_roots": [
                {"policy_id": root.policy_id, "path": str(root.path), "source": root.source}
                for root in self.protected_roots
            ],
            "security_boundary": "runtime_guard_and_transcript_audit_not_os_sandbox",
        }
        if include_digest:
            payload["policy_digest"] = self.digest
        return payload


@dataclass(frozen=True)
class ContaminationAudit:
    status: str = "clean"
    findings: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "finding_count": len(self.findings),
            "findings": [dict(finding) for finding in self.findings],
        }


@dataclass(frozen=True)
class WorkspaceAudit:
    audit_execution_status: str = "complete"
    boundary_status: str = "clean"
    contamination_status: str = "clear"
    adjudication: str = "scoreable"
    findings: tuple[dict[str, Any], ...] = ()
    recovery: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.audit_execution_status not in {"complete", "unavailable"}:
            raise ValueError(f"Unsupported audit execution status: {self.audit_execution_status}")
        if self.boundary_status not in {"clean", "warning", "violated", "unknown"}:
            raise ValueError(f"Unsupported workspace boundary status: {self.boundary_status}")
        if self.contamination_status not in {"clear", "confirmed", "indeterminate"}:
            raise ValueError(f"Unsupported contamination status: {self.contamination_status}")
        if self.adjudication not in {"scoreable", "scoreable_degraded", "non_evaluable"}:
            raise ValueError(f"Unsupported workspace adjudication: {self.adjudication}")

    @property
    def status(self) -> str:
        """Legacy read adapter; new writers persist the independent status axes."""
        if self.audit_execution_status == "unavailable":
            return "unavailable"
        if self.contamination_status != "clear":
            return "contaminated"
        return "clean"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_ISOLATION_SCHEMA_VERSION,
            "audit_execution_status": self.audit_execution_status,
            "boundary_status": self.boundary_status,
            "contamination_status": self.contamination_status,
            "adjudication": self.adjudication,
            "finding_count": len(self.findings),
            "findings": [dict(finding) for finding in self.findings],
            "recovery": dict(self.recovery),
        }


def adjudicate_workspace_findings(
    findings: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    *,
    audit_execution_status: str = "complete",
    recovery: Mapping[str, Any] | None = None,
) -> WorkspaceAudit:
    normalized_findings = tuple(dict(finding) for finding in findings)
    if audit_execution_status == "unavailable":
        return WorkspaceAudit(
            audit_execution_status="unavailable",
            boundary_status="unknown",
            contamination_status="indeterminate",
            adjudication="non_evaluable",
            findings=normalized_findings,
            recovery=dict(recovery or {}),
        )

    boundary_effects = {str(finding.get("boundary_effect") or "clean") for finding in normalized_findings}
    if "violated" in boundary_effects:
        boundary_status = "violated"
    elif "unknown" in boundary_effects:
        boundary_status = "unknown"
    elif "warning" in boundary_effects:
        boundary_status = "warning"
    else:
        boundary_status = "clean"

    exposures = {str(finding.get("information_exposure") or "none") for finding in normalized_findings}
    if "confirmed" in exposures:
        contamination_status = "confirmed"
    elif exposures & {"possible", "unknown", "indeterminate"}:
        contamination_status = "indeterminate"
    else:
        contamination_status = "clear"

    if contamination_status != "clear":
        adjudication = "non_evaluable"
    elif boundary_status in {"violated", "unknown"}:
        adjudication = "scoreable_degraded"
    else:
        adjudication = "scoreable"
    return WorkspaceAudit(
        audit_execution_status="complete",
        boundary_status=boundary_status,
        contamination_status=contamination_status,
        adjudication=adjudication,
        findings=normalized_findings,
        recovery=dict(recovery or {}),
    )


def ensure_workspace_audit(audit: WorkspaceAudit | ContaminationAudit) -> WorkspaceAudit:
    if isinstance(audit, WorkspaceAudit):
        return audit
    if audit.status == "unavailable":
        return adjudicate_workspace_findings(
            tuple(dict(finding) for finding in audit.findings),
            audit_execution_status="unavailable",
        )
    if audit.status == "contaminated":
        findings = tuple(
            {
                "boundary_effect": "violated",
                "information_exposure": "confirmed",
                **dict(finding),
            }
            for finding in audit.findings
        )
        return adjudicate_workspace_findings(findings)
    return adjudicate_workspace_findings(tuple(dict(finding) for finding in audit.findings))


def build_workspace_access_policy(
    *,
    active_workspace: Path,
    role: str,
    skills_enabled: bool,
    protected_roots: tuple[ProtectedRoot, ...],
    always_read_scopes: tuple[Path, ...] | list[Path] = (),
    skill_read_scopes: tuple[Path, ...] | list[Path] = (),
    extra_write_scopes: tuple[Path, ...] | list[Path] = (),
    extra_exec_workdir_scopes: tuple[Path, ...] | list[Path] = (),
) -> WorkspaceAccessPolicy:
    workspace = Path(active_workspace).expanduser().resolve(strict=False)
    scratch = workspace / "scratch"

    def scope(scope_id: str, path: Path, source: str) -> AccessScope:
        resolved = Path(path).expanduser().resolve(strict=False)
        kind = "file" if resolved.is_file() else "directory"
        return AccessScope(scope_id=scope_id, path=resolved, kind=kind, source=source)

    external_reads = [Path(path) for path in always_read_scopes]
    if role != "judge" and (role != "single_llm" or skills_enabled):
        external_reads.extend(Path(path) for path in skill_read_scopes)
    reads = [scope("active_workspace", workspace, "active_workspace")]
    reads.extend(
        scope(f"role_read_{index}", path, "runner.role_read_scope")
        for index, path in enumerate(external_reads)
    )
    writes = [scope("attempt_scratch", scratch, "active_workspace.scratch")]
    writes.extend(
        scope(f"role_write_{index}", path, "runner.role_write_scope")
        for index, path in enumerate(extra_write_scopes)
    )
    exec_scopes = [
        scope("active_workspace", workspace, "active_workspace"),
        scope("attempt_scratch", scratch, "active_workspace.scratch"),
    ]
    exec_scopes.extend(
        scope(f"role_exec_{index}", path, "runner.role_exec_scope")
        for index, path in enumerate(extra_exec_workdir_scopes)
    )
    return WorkspaceAccessPolicy(
        schema_version=WORKSPACE_POLICY_SCHEMA_VERSION,
        role=role,
        skills_enabled=bool(skills_enabled),
        read_scopes=tuple(reads),
        write_scopes=tuple(writes),
        exec_workdir_scopes=tuple(exec_scopes),
        protected_roots=protected_roots,
    )


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _normalize_access_scopes(
    scopes: tuple[AccessScope, ...] | list[AccessScope],
) -> tuple[AccessScope, ...]:
    normalized: dict[tuple[str, str, str], AccessScope] = {}
    for scope in scopes:
        scope_id = str(scope.scope_id or "").strip()
        source = str(scope.source or "").strip()
        kind = str(scope.kind or "").strip().lower()
        if not scope_id:
            raise ValueError("Workspace access scope id must be non-empty.")
        if kind not in {"directory", "file"}:
            raise ValueError(f"Unsupported workspace access scope kind: {scope.kind}")
        resolved = Path(scope.path).expanduser().resolve(strict=False)
        if resolved == Path(resolved.anchor):
            raise ValueError("Filesystem root cannot be used as a workspace access scope.")
        if resolved.exists():
            if kind == "directory" and not resolved.is_dir():
                raise ValueError(f"Directory access scope is not a directory: {resolved}")
            if kind == "file" and not resolved.is_file():
                raise ValueError(f"File access scope is not a file: {resolved}")
        key = (scope_id, str(resolved), kind)
        candidate = AccessScope(scope_id=scope_id, path=resolved, kind=kind, source=source)
        previous = normalized.get(key)
        if previous is None or candidate.source < previous.source:
            normalized[key] = candidate
    return tuple(
        sorted(normalized.values(), key=lambda item: (item.scope_id, str(item.path), item.kind, item.source))
    )


def _access_scope_matches(candidate: Path, scope: AccessScope) -> bool:
    resolved = Path(candidate).expanduser().resolve(strict=False)
    if scope.kind == "file":
        return resolved == scope.path
    return _path_is_within(resolved, scope.path)


def _normalize_protected_roots(
    roots: tuple[ProtectedRoot, ...] | list[ProtectedRoot],
) -> tuple[ProtectedRoot, ...]:
    normalized: list[ProtectedRoot] = []
    for root in roots:
        policy_id = str(root.policy_id or "").strip()
        source = str(root.source or "").strip()
        raw_path = Path(root.path).expanduser()
        resolved = raw_path.resolve(strict=False)
        if not policy_id:
            raise ValueError("Protected benchmark root policy_id must be non-empty.")
        if str(root.path).strip() in {"", "."} or resolved == Path(resolved.anchor):
            raise ValueError(
                f"Protected benchmark root `{policy_id}` must not be empty or the filesystem root: {root.path}"
            )
        if resolved.exists() and not resolved.is_dir():
            raise ValueError(
                f"Existing protected benchmark root `{policy_id}` must be a directory: {resolved}"
            )
        normalized.append(ProtectedRoot(policy_id=policy_id, path=resolved, source=source))

    deduped: dict[tuple[str, str], ProtectedRoot] = {}
    for root in sorted(normalized, key=lambda item: (item.policy_id, str(item.path), item.source)):
        deduped.setdefault((root.policy_id, str(root.path)), root)
    return tuple(sorted(deduped.values(), key=lambda item: (item.policy_id, str(item.path), item.source)))

