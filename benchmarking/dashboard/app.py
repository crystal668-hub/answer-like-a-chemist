from __future__ import annotations

import argparse
import mimetypes
from pathlib import Path
from typing import Any

import runtime_paths
from benchmarking.dashboard.annotations import AnnotationStore
from benchmarking.dashboard.service import (
    AssetAccessError,
    BenchmarkDashboard,
    RecordNotFoundError,
    RunNotFoundError,
)


FASTAPI_EXTRA_MESSAGE = (
    "Benchmark dashboard requires the web-ui optional dependencies. "
    "Run it with: uv run --extra web-ui python -m benchmarking.dashboard.app"
)


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def _import_fastapi() -> tuple[Any, ...]:
    try:
        from fastapi import Body, FastAPI, HTTPException
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
    except ModuleNotFoundError as exc:
        raise RuntimeError(FASTAPI_EXTRA_MESSAGE) from exc
    return FastAPI, HTTPException, Body, FileResponse, StaticFiles


def create_app(
    *,
    run_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    annotation_db: str | Path | None = None,
) -> Any:
    FastAPI, HTTPException, Body, FileResponse, StaticFiles = _import_fastapi()

    store = AnnotationStore(annotation_db or runtime_paths.project_state_root / "benchmark-dashboard" / "dashboard.sqlite")
    dashboard = BenchmarkDashboard(run_roots=run_roots, annotation_store=store)
    app = FastAPI(title="OpenClaw Benchmark Dashboard")

    @app.get("/api/runs")
    def api_list_runs(include_hidden: bool = False) -> list[dict[str, Any]]:
        return dashboard.list_runs(include_hidden=include_hidden)

    @app.get("/api/runs/{run_id}")
    def api_get_run(run_id: str) -> dict[str, Any]:
        try:
            return dashboard.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/runs/{run_id}")
    def api_patch_run(run_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return store.upsert_run_metadata(
            run_id=run_id,
            alias=payload.get("alias"),
            favorite=payload.get("favorite"),
            hidden=payload.get("hidden"),
        )

    @app.get("/api/runs/{run_id}/records")
    def api_list_records(run_id: str) -> list[dict[str, Any]]:
        try:
            return dashboard.list_records(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/records/{record_id}")
    def api_get_record(run_id: str, record_id: str) -> dict[str, Any]:
        try:
            return dashboard.get_record(run_id, record_id)
        except (RunNotFoundError, RecordNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/progress")
    def api_get_progress(run_id: str) -> dict[str, Any]:
        try:
            return dashboard.get_run(run_id)["progress"]
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id}/assets/{asset_path:path}")
    def api_get_asset(run_id: str, asset_path: str) -> Any:
        try:
            path = dashboard.resolve_asset(run_id, asset_path)
        except (RunNotFoundError, AssetAccessError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0])

    @app.post("/api/annotations")
    def api_create_annotation(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return store.create_annotation(
            run_id=str(payload.get("run_id") or ""),
            record_id=str(payload.get("record_id") or ""),
            group_id=str(payload.get("group_id") or ""),
            note=str(payload.get("note") or ""),
            status=str(payload.get("status") or ""),
            tags=payload.get("tags") if isinstance(payload.get("tags"), list) else [],
            manual_verdict=str(payload.get("manual_verdict") or ""),
        )

    @app.patch("/api/annotations/{annotation_id}")
    def api_patch_annotation(annotation_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            allowed = {"note", "status", "tags", "manual_verdict", "deleted"}
            updates = {key: value for key, value in payload.items() if key in allowed and value is not None}
            return store.update_annotation(annotation_id, **updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/annotations/{annotation_id}")
    def api_delete_annotation(annotation_id: int) -> dict[str, Any]:
        try:
            return store.delete_annotation(annotation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")

    @app.get("/")
    def index() -> Any:
        return FileResponse(STATIC_ROOT / "index.html")

    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local OpenClaw benchmark dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--run-root",
        action="append",
        dest="run_roots",
        help="Benchmark run root to scan. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--annotation-db",
        default=str(runtime_paths.project_state_root / "benchmark-dashboard" / "dashboard.sqlite"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        raise RuntimeError(FASTAPI_EXTRA_MESSAGE) from exc
    app = create_app(run_roots=args.run_roots, annotation_db=args.annotation_db)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
