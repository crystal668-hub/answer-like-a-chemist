from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from benchmarking.dashboard.app import create_app
from tests.test_benchmark_dashboard import write_demo_run


def test_dashboard_app_module_imports_without_fastapi_extra() -> None:
    module = importlib.import_module("benchmarking.dashboard.app")

    assert "uv run --extra web-ui" in module.FASTAPI_EXTRA_MESSAGE


def test_dashboard_static_frontend_contains_dashboard_shell() -> None:
    static_root = Path(__file__).resolve().parents[1] / "benchmarking" / "dashboard" / "static"
    index = (static_root / "index.html").read_text(encoding="utf-8")
    script = (static_root / "app.js").read_text(encoding="utf-8")
    styles = (static_root / "styles.css").read_text(encoding="utf-8")

    assert "Benchmark Monitor" in index
    assert "run-list" in index
    assert "record-list" in index
    assert "/static/app.js?v=20260918-observability-v1" in index
    assert "setInterval(refreshProgress" in script
    assert "function renderInlineMarkdown" in script
    assert "asset-image" in script
    assert "track-filter" in index
    assert "dataset-filter" not in index
    assert "subset-filter" not in index
    assert "function renderComparison" in script
    assert "function renderActiveAttempts" in script
    assert "hide-run" in index
    assert 'class="refresh-icon"' in index
    assert 'button.classList.add("is-refreshing")' in script
    assert 'button.setAttribute("aria-busy", "true")' in script
    assert "animation: refresh-spin 700ms linear infinite" in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "api/annotations" in script
    assert "function renderRecordScoreBadges" in script
    assert "score-strip" in script
    assert "function renderRunScoreComparison" in script
    assert "single_llm_skills_on" in script
    assert "single_llm_skills_off" in script
    assert "Δ" in script
    assert "function renderExec" in script
    assert "function renderPackages" in script
    assert "function renderTokens" in script
    assert "function renderResources" in script
    assert "function renderRecordDetail" in script
    assert "setInterval(refreshProgress, 5000)" in script
    assert 'data-tab="timeline"' in index
    assert 'data-tab="resources"' in index


def test_dashboard_static_assets_disable_browser_cache(tmp_path: Path) -> None:
    testclient = pytest.importorskip("fastapi.testclient")
    app = create_app(run_roots=[tmp_path], annotation_db=tmp_path / "dashboard.sqlite")
    client = testclient.TestClient(app)

    assert client.get("/api/tracks").json() == [
        "open_generation_rdkit",
        "open_generation_xtb",
        "property_calculation_advanced",
        "property_calculation_basic",
    ]

    index = client.get("/")
    script = client.get("/static/app.js")

    assert index.status_code == 200
    assert script.status_code == 200
    assert "no-store" in index.headers.get("cache-control", "")
    assert "no-store" in script.headers.get("cache-control", "")


def test_dashboard_api_supports_run_metadata_and_annotation_crud(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    testclient = pytest.importorskip("fastapi.testclient")

    app = create_app(run_roots=[tmp_path], annotation_db=tmp_path / "dashboard.sqlite")
    client = testclient.TestClient(app)

    patched = client.patch(f"/api/runs/{run_root.name}", json={"alias": "Smoke", "favorite": True, "hidden": True})
    assert patched.status_code == 200
    assert patched.json()["alias"] == "Smoke"
    assert client.get("/api/runs").json() == []
    visible = client.get("/api/runs?include_hidden=true").json()
    assert visible[0]["alias"] == "Smoke"
    assert visible[0]["hidden"] is True
    assert visible[0]["tracks"] == ["open_generation_rdkit"]
    assert "observability" in visible[0]
    assert "datasets" not in visible[0] and "subsets" not in visible[0]

    created = client.post(
        "/api/annotations",
        json={
            "run_id": run_root.name,
            "record_id": "r1",
            "group_id": "single_llm_skills_on",
            "note": "needs review",
            "status": "needs_review",
            "tags": ["manual"],
            "manual_verdict": "uncertain",
        },
    )
    assert created.status_code == 200
    annotation_id = created.json()["id"]
    updated = client.patch(f"/api/annotations/{annotation_id}", json={"note": "checked", "tags": ["done"]})
    assert updated.status_code == 200
    assert updated.json()["note"] == "checked"
    record = client.get(f"/api/runs/{run_root.name}/records/r1").json()
    assert record["annotations"][0]["tags"] == ["done"]
    deleted = client.delete(f"/api/annotations/{annotation_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/runs/{run_root.name}/records/r1").json()["annotations"] == []


def test_dashboard_asset_api_rejects_path_traversal(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    testclient = pytest.importorskip("fastapi.testclient")

    app = create_app(run_roots=[tmp_path], annotation_db=tmp_path / "dashboard.sqlite")
    client = testclient.TestClient(app)

    response = client.get(f"/api/runs/{run_root.name}/assets/../outside.txt")

    assert response.status_code == 404


def test_dashboard_monitor_and_attempt_resource_api(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    result_path = next((run_root / "per-record").glob("*/*.json"))
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    resources_path = run_root / "observability/attempts/single_llm_skills_on/r1/attempt-0-deadbeef/resources.jsonl"
    resources_path.parent.mkdir(parents=True)
    resources_path.write_text(
        "\n".join(
            json.dumps({"window_index": index, "cpu_peak_percent": index, "memory_peak_bytes": index * 10})
            for index in range(8)
        ) + "\n",
        encoding="utf-8",
    )
    summary_path = resources_path.with_name("summary.json")
    summary_path.write_text("{}", encoding="utf-8")
    payload["schema_version"] = 5
    payload["observability"] = {
        "schema_version": 1,
        "coverage": {key: "exact" for key in ("timing", "tokens", "tools", "packages", "resources")},
        "totals": {
            "timing": {"record_wall_seconds": 3},
            "tokens": {"total_tokens": 12},
            "tools": {"call_count": 1, "failure_count": 0},
            "packages": {"added_package_count": 0},
            "resources": {"memory_peak_bytes": 70},
        },
        "attempt_count": 1,
        "attempts": [{
            "attempt_index": 0,
            "summary_path": str(summary_path.relative_to(run_root)),
            "resources_path": str(resources_path.relative_to(run_root)),
            "coverage": {"resources": "exact"},
        }],
    }
    result_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    active = run_root / "observability/active/a.json"
    active.parent.mkdir(parents=True)
    active.write_text(
        json.dumps({
            "schema_version": 1,
            "identity": {"record_id": "r1", "group_id": "single_llm_skills_on", "attempt_index": 0},
            "resources_path": str(resources_path.relative_to(run_root)),
        }),
        encoding="utf-8",
    )
    testclient = pytest.importorskip("fastapi.testclient")
    client = testclient.TestClient(create_app(run_roots=[tmp_path], annotation_db=tmp_path / "dashboard.sqlite"))

    monitor = client.get(f"/api/runs/{run_root.name}/monitor")
    resources = client.get(
        f"/api/runs/{run_root.name}/records/r1/groups/single_llm_skills_on/attempts/0/resources?max_points=4"
    )

    assert monitor.status_code == 200
    assert monitor.json()["active_attempts"][0]["latest_resource_window"]["window_index"] == 7
    assert resources.status_code == 200
    points = resources.json()["points"]
    assert len(points) <= 4
    assert {0, 7}.issubset({item["window_index"] for item in points})
    assert 7 in {item["memory_peak_bytes"] // 10 for item in points}


def test_attempt_resource_api_rejects_symlink_artifact(tmp_path: Path) -> None:
    run_root = write_demo_run(tmp_path)
    result_path = next((run_root / "per-record").glob("*/*.json"))
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"window_index":0}\n', encoding="utf-8")
    linked = run_root / "observability/resources.jsonl"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    payload["schema_version"] = 5
    payload["observability"] = {
        "schema_version": 1,
        "coverage": {"resources": "exact"},
        "totals": {},
        "attempts": [{
            "attempt_index": 0,
            "resources_path": str(linked.relative_to(run_root)),
            "coverage": {"resources": "exact"},
        }],
    }
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    testclient = pytest.importorskip("fastapi.testclient")
    client = testclient.TestClient(create_app(run_roots=[tmp_path], annotation_db=tmp_path / "dashboard.sqlite"))

    response = client.get(
        f"/api/runs/{run_root.name}/records/r1/groups/single_llm_skills_on/attempts/0/resources"
    )

    assert response.status_code == 200
    assert response.json()["points"] == []
