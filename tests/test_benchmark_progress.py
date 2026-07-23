from __future__ import annotations

import json
from pathlib import Path

from benchmarking.dashboard import progress as dashboard_progress


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_progress_writer_records_events_and_state_snapshot(tmp_path: Path) -> None:
    writer = dashboard_progress.ProgressWriter(tmp_path, total_records=2, groups=["single_llm_skills_on"])

    writer.run_started()
    writer.group_started("single_llm_skills_on")
    writer.record_started("single_llm_skills_on", "r1", index=1)
    writer.record_completed("single_llm_skills_on", "r1", status="completed", score=1.0)

    events = [json.loads(line) for line in (tmp_path / "progress" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    state = json.loads((tmp_path / "progress" / "state.json").read_text(encoding="utf-8"))

    assert [event["type"] for event in events] == [
        "run_started",
        "group_started",
        "record_started",
        "record_completed",
    ]
    assert state["groups"]["single_llm_skills_on"]["completed_records"] == ["r1"]
    assert state["groups"]["single_llm_skills_on"]["current_record_id"] is None
    assert state["completed"] == 1
    assert state["total"] == 2


def test_load_progress_prefers_state_json(tmp_path: Path) -> None:
    write_json(
        tmp_path / "progress" / "state.json",
        {
            "status": "running",
            "total": 4,
            "completed": 1,
            "groups": {
                "single_llm_skills_on": {
                    "status": "running",
                    "current_record_id": "r2",
                    "completed_records": ["r1"],
                }
            },
        },
    )

    progress = dashboard_progress.load_progress(tmp_path, expected_total=4, group_ids=["single_llm_skills_on"])

    assert progress["status"] == "running"
    assert progress["completed"] == 1
    assert progress["groups"]["single_llm_skills_on"]["current_record_id"] == "r2"


def test_load_progress_falls_back_to_per_record_and_waves(tmp_path: Path) -> None:
    write_json(tmp_path / "per-record" / "single_llm_skills_on" / "r1.json", {"record_id": "r1"})
    write_json(tmp_path / "per-record" / "single_llm_skills_off" / "r1.json", {"record_id": "r1"})
    write_json(
        tmp_path / "waves" / "wave-01.json",
        {
            "wave_index": 1,
            "groups": ["single_llm_skills_on", "single_llm_skills_off"],
            "status": "running",
            "started_at": "2026-06-04T12:00:00+0800",
        },
    )

    progress = dashboard_progress.load_progress(
        tmp_path,
        expected_total=4,
        group_ids=["single_llm_skills_on", "single_llm_skills_off"],
    )

    assert progress["status"] == "running"
    assert progress["completed"] == 2
    assert progress["total"] == 4
    assert progress["groups"]["single_llm_skills_on"]["completed_count"] == 1
    assert progress["groups"]["single_llm_skills_off"]["completed_count"] == 1


def test_load_progress_never_reports_total_below_completed_count(tmp_path: Path) -> None:
    write_json(tmp_path / "per-record" / "single_llm_skills_on" / "r1.json", {"record_id": "r1"})
    write_json(tmp_path / "per-record" / "single_llm_skills_on" / "r2.json", {"record_id": "r2"})

    progress = dashboard_progress.load_progress(tmp_path, expected_total=1, group_ids=["single_llm_skills_on"])

    assert progress["completed"] == 2
    assert progress["total"] == 2


def test_progress_cancellation_is_terminal_and_idempotent(tmp_path: Path) -> None:
    writer = dashboard_progress.ProgressWriter(tmp_path, total_records=1, groups=["g1"])
    writer.run_started()
    writer.group_started("g1")
    writer.record_started("g1", "r1", index=1)
    writer.run_cancelling(reason={"source": "signal", "signal": "SIGINT"})
    writer.record_cancelled("g1", "r1")
    writer.group_cancelled("g1")
    writer.run_cancelled(errors=[])
    writer.run_cancelled(errors=[])
    writer.run_completed()

    state = json.loads((tmp_path / "progress" / "state.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (tmp_path / "progress" / "events.jsonl").read_text().splitlines()]
    assert state["status"] == "cancelled"
    assert state["groups"]["g1"]["status"] == "cancelled"
    assert state["groups"]["g1"]["cancelled_records"] == ["r1"]
    assert [event["type"] for event in events].count("run_cancelled") == 1
    assert all(event["type"] != "run_completed" for event in events)


def test_load_progress_reconciles_dead_owner_to_cancelled_with_errors(tmp_path: Path) -> None:
    write_json(
        tmp_path / "progress" / "state.json",
        {
            "status": "running",
            "owner_pid": 2**31 - 1,
            "total": 1,
            "completed": 0,
            "groups": {
                "g1": {
                    "status": "running",
                    "current_record_id": "r1",
                    "completed_records": [],
                }
            },
        },
    )
    write_json(tmp_path / "waves" / "wave-01.json", {"status": "running", "groups": ["g1"]})

    progress = dashboard_progress.load_progress(tmp_path, expected_total=1, group_ids=["g1"])
    persisted = json.loads((tmp_path / "progress" / "state.json").read_text(encoding="utf-8"))
    wave = json.loads((tmp_path / "waves" / "wave-01.json").read_text(encoding="utf-8"))
    manifest = json.loads((tmp_path / "runtime-manifest.json").read_text(encoding="utf-8"))

    assert progress["status"] == "cancelled_with_errors"
    assert progress["groups"]["g1"]["status"] == "cancelled"
    assert persisted["status"] == "cancelled_with_errors"
    assert persisted["errors"][0]["code"] == "stale_run_owner_missing"
    assert wave["status"] == "cancelled"
    assert manifest["terminal_status"] == "cancelled_with_errors"
