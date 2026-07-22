#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime_lease import (
    LEASE_KIND,
    MANIFEST_KIND,
    cleanup_report_filename_for_run,
    iso_now,
    lease_dir_from_manifest,
    maybe_int,
    read_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean benchmark run-scoped processes, sessions, and state.")
    parser.add_argument("--manifest", help="Cleanup manifest path")
    parser.add_argument("--run-id", help="Manual run id fallback")
    parser.add_argument("--kind", choices=("chemqa",), help="Manual benchmark kind")
    parser.add_argument("--output-root", help="Manual output root")
    parser.add_argument("--grace-seconds", type=float, default=5.0)
    parser.add_argument("--kill-after-seconds", type=float, default=10.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


@dataclass
class CleanupContext:
    manifest: dict[str, Any]
    manifest_path: Path | None


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    pgid: int
    command: str


def load_context(args: argparse.Namespace) -> CleanupContext:
    if args.manifest:
        path = Path(args.manifest).expanduser().resolve()
        payload = read_json(path)
        if str(payload.get("kind") or "") != MANIFEST_KIND:
            raise SystemExit(f"Manifest has unexpected kind: {path}")
        return CleanupContext(manifest=payload, manifest_path=path)
    if not args.run_id or not args.kind or not args.output_root:
        raise SystemExit("Manual mode requires --run-id, --kind, and --output-root.")
    output_root = Path(args.output_root).expanduser().resolve()
    payload = {
        "kind": MANIFEST_KIND,
        "version": 1,
        "run_id": args.run_id,
        "benchmark_kind": args.kind,
        "group_id": "",
        "output_root": str(output_root),
        "launch_home": "",
        "clawteam_data_dir": "",
        "session_assignments": {},
        "control_roots": [],
        "generated_roots": [],
        "artifact_roots": [],
        "lease_dir": str(output_root / "cleanroom" / "leases"),
        "created_at": iso_now(),
        "updated_at": iso_now(),
    }
    return CleanupContext(manifest=payload, manifest_path=None)


def parse_process_snapshot(raw: str) -> list[ProcessInfo]:
    processes: list[ProcessInfo] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 2)
        if len(parts) < 3:
            continue
        pid = maybe_int(parts[0])
        pgid = maybe_int(parts[1])
        command = parts[2].strip()
        if pid <= 0 or not command:
            continue
        processes.append(ProcessInfo(pid=pid, pgid=pgid, command=command))
    return processes


def process_snapshot() -> tuple[list[ProcessInfo], list[str]]:
    try:
        result = subprocess.run(
            ["ps", "axww", "-o", "pid=", "-o", "pgid=", "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return [], [f"Unable to enumerate processes with ps: {exc}"]
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        detail = stderr or f"exit status {result.returncode}"
        return [], [f"Unable to enumerate processes with ps: {detail}"]
    return parse_process_snapshot(result.stdout), []


def safe_read_cmdline(pid: int, *, snapshot: dict[int, ProcessInfo] | None = None) -> str:
    if pid <= 0:
        return ""
    if snapshot is not None:
        info = snapshot.get(pid)
        if info is not None:
            return info.command
    processes, warnings = process_snapshot()
    if warnings:
        return ""
    for info in processes:
        if info.pid == pid:
            return info.command
    return ""


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def iter_lease_payloads(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    lease_dir = lease_dir_from_manifest(manifest)
    if not lease_dir.is_dir():
        return []
    payloads: list[dict[str, Any]] = []
    run_id = str(manifest.get("run_id") or "")
    for path in sorted(lease_dir.glob("*.lease.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if str(payload.get("kind") or "") != LEASE_KIND:
            continue
        if str(payload.get("run_id") or "") != run_id:
            continue
        payload["_lease_path"] = str(path)
        payloads.append(payload)
    return payloads


def process_targets(
    manifest: dict[str, Any],
    lease_payloads: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    run_id = str(manifest.get("run_id") or "")
    session_ids = {str(value).strip() for value in dict(manifest.get("session_assignments") or {}).values() if str(value).strip()}
    pid_targets: dict[int, dict[str, Any]] = {}
    pgid_targets: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    current_pid = os.getpid()
    current_pgid = 0
    try:
        current_pgid = os.getpgid(0)
    except Exception:
        current_pgid = 0
    snapshot_items, snapshot_warnings = process_snapshot()
    warnings.extend(snapshot_warnings)
    snapshot = {item.pid: item for item in snapshot_items}

    for lease in lease_payloads:
        pid = maybe_int(lease.get("pid"))
        pgid = maybe_int(lease.get("pgid"))
        role = str(lease.get("role") or "")
        slot = str(lease.get("slot") or "")
        session_id = str(lease.get("session_id") or "")
        if pid > 0 and pid != current_pid:
            pid_targets.setdefault(
                pid,
                {
                    "pid": pid,
                    "pgid": pgid,
                    "role": role,
                    "slot": slot,
                    "session_id": session_id,
                    "source": "lease",
                    "cmdline": safe_read_cmdline(pid, snapshot=snapshot),
                },
            )
        if pgid > 0 and pgid != current_pgid and pgid != current_pid:
            pgid_targets.setdefault(
                pgid,
                {
                    "pgid": pgid,
                    "source": "lease",
                    "role": role,
                    "slot": slot,
                    "session_id": session_id,
                },
            )

    for info in snapshot_items:
        pid = info.pid
        if pid == current_pid:
            continue
        cmdline = info.command
        if not cmdline:
            continue
        matches_run = run_id and run_id in cmdline
        matches_session = any(session_id in cmdline for session_id in session_ids)
        if not matches_run and not matches_session:
            continue
        pgid = info.pgid
        pid_targets.setdefault(
            pid,
            {
                "pid": pid,
                "pgid": pgid,
                "role": "",
                "slot": "",
                "session_id": "",
                "source": "proc-scan",
                "cmdline": cmdline,
            },
        )
        if pgid > 0 and pgid != current_pgid and pgid != current_pid:
            pgid_targets.setdefault(pgid, {"pgid": pgid, "source": "proc-scan"})

    return list(pgid_targets.values()), list(pid_targets.values()), warnings


def terminate_process_groups(
    groups: list[dict[str, Any]],
    *,
    sig: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in groups:
        pgid = maybe_int(item.get("pgid"))
        if pgid <= 0:
            continue
        payload = {"pgid": pgid, "signal": sig, "sent": False}
        if dry_run:
            payload["sent"] = True
            results.append(payload)
            continue
        try:
            os.killpg(pgid, sig)
            payload["sent"] = True
        except ProcessLookupError:
            payload["sent"] = False
            payload["missing"] = True
        except Exception as exc:
            payload["error"] = str(exc)
        results.append(payload)
    return results


def terminate_pids(
    targets: list[dict[str, Any]],
    *,
    sig: int,
    dry_run: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in targets:
        pid = maybe_int(item.get("pid"))
        if pid <= 0:
            continue
        payload = {"pid": pid, "signal": sig, "sent": False}
        if dry_run:
            payload["sent"] = True
            results.append(payload)
            continue
        try:
            os.kill(pid, sig)
            payload["sent"] = True
        except ProcessLookupError:
            payload["sent"] = False
            payload["missing"] = True
        except Exception as exc:
            payload["error"] = str(exc)
        results.append(payload)
    return results


def wait_for_exit(targets: list[dict[str, Any]], *, timeout_seconds: float) -> list[int]:
    deadline = time.time() + max(0.0, timeout_seconds)
    pids = sorted({maybe_int(item.get("pid")) for item in targets if maybe_int(item.get("pid")) > 0})
    remaining = set(pids)
    while remaining and time.time() < deadline:
        finished: list[int] = []
        for pid in list(remaining):
            if not pid_exists(pid):
                finished.append(pid)
        for pid in finished:
            remaining.discard(pid)
        if remaining:
            time.sleep(0.2)
    return sorted(remaining)


def cleanup(context: CleanupContext, *, grace_seconds: float, kill_after_seconds: float, dry_run: bool) -> dict[str, Any]:
    manifest = context.manifest
    run_id = str(manifest.get("run_id") or "")
    lease_payloads = iter_lease_payloads(manifest)
    process_groups, pid_targets, process_warnings = process_targets(manifest, lease_payloads)

    report: dict[str, Any] = {
        "run_id": run_id,
        "manifest_path": str(context.manifest_path) if context.manifest_path else "",
        "dry_run": dry_run,
        "started_at": iso_now(),
        "lease_count": len(lease_payloads),
        "process_groups": process_groups,
        "pid_targets": pid_targets,
        "termination": {},
        "session_store_scrub": [],
        "removed_paths": [],
        "warnings": list(process_warnings),
        "errors": [],
    }

    report["termination"]["term_groups"] = terminate_process_groups(process_groups, sig=signal.SIGTERM, dry_run=dry_run)
    report["termination"]["term_pids"] = terminate_pids(pid_targets, sig=signal.SIGTERM, dry_run=dry_run)
    remaining_after_term = [] if dry_run else wait_for_exit(pid_targets, timeout_seconds=grace_seconds)
    report["termination"]["remaining_after_term"] = remaining_after_term

    if remaining_after_term:
        kill_targets = [item for item in pid_targets if maybe_int(item.get("pid")) in set(remaining_after_term)]
        report["termination"]["kill_groups"] = terminate_process_groups(process_groups, sig=signal.SIGKILL, dry_run=dry_run)
        report["termination"]["kill_pids"] = terminate_pids(kill_targets, sig=signal.SIGKILL, dry_run=dry_run)
        remaining_after_kill = [] if dry_run else wait_for_exit(kill_targets, timeout_seconds=max(0.0, kill_after_seconds))
    else:
        remaining_after_kill = []
        report["termination"]["kill_groups"] = []
        report["termination"]["kill_pids"] = []
    report["termination"]["remaining_after_kill"] = remaining_after_kill

    report["retention_policy"] = "processes_only_preserve_sessions_and_artifacts"

    remaining_processes = []
    if not dry_run:
        snapshot_items, snapshot_warnings = process_snapshot()
        snapshot = {item.pid: item for item in snapshot_items}
        report["warnings"].extend(snapshot_warnings)
        for item in pid_targets:
            pid = maybe_int(item.get("pid"))
            if pid_exists(pid):
                remaining_processes.append({"pid": pid, "cmdline": safe_read_cmdline(pid, snapshot=snapshot)})
    report["postcheck"] = {
        "remaining_processes": remaining_processes,
        "remaining_session_entries": [],
    }
    report["completed_at"] = iso_now()
    report["success"] = not remaining_processes and not report["errors"]
    return report


def write_report(manifest: dict[str, Any], report: dict[str, Any]) -> Path | None:
    output_root = str(manifest.get("output_root") or "").strip()
    if not output_root:
        return None
    path = Path(output_root).expanduser().resolve() / "cleanroom" / "reports" / cleanup_report_filename_for_run(str(manifest.get("run_id") or "run"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    try:
        context = load_context(args)
        report = cleanup(
            context,
            grace_seconds=max(0.0, float(args.grace_seconds)),
            kill_after_seconds=max(0.0, float(args.kill_after_seconds)),
            dry_run=args.dry_run,
        )
        report_path = write_report(context.manifest, report)
        if report_path is not None:
            report["report_path"] = str(report_path)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"cleanup success={report['success']} run_id={report['run_id']}")
        return 0 if report["success"] else 1
    except Exception as exc:
        if args.json:
            payload = {
                "run_id": str(getattr(locals().get("context", None), "manifest", {}).get("run_id") or args.run_id or ""),
                "manifest_path": str(getattr(locals().get("context", None), "manifest_path", "") or ""),
                "dry_run": bool(args.dry_run),
                "started_at": iso_now(),
                "completed_at": iso_now(),
                "lease_count": 0,
                "process_groups": [],
                "pid_targets": [],
                "termination": {},
                "session_store_scrub": [],
                "removed_paths": [],
                "warnings": [],
                "errors": [f"Unhandled cleanup failure: {exc}"],
                "postcheck": {"remaining_processes": [], "remaining_session_entries": []},
                "success": False,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 1
        raise


if __name__ == "__main__":
    raise SystemExit(main())
