from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _json_list(values: list[str] | tuple[str, ...] | None) -> str:
    return json.dumps(list(values or ()), ensure_ascii=False)


def _row_to_annotation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "run_id": row["run_id"],
        "record_id": row["record_id"],
        "group_id": row["group_id"],
        "note": row["note"],
        "status": row["status"],
        "tags": json.loads(row["tags_json"] or "[]"),
        "manual_verdict": row["manual_verdict"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "deleted": bool(row["deleted"]),
    }


class AnnotationStore:
    """SQLite-backed dashboard metadata store.

    Benchmark result files stay immutable. This store owns only local review
    metadata such as aliases, favorites, notes, tags, and soft deletes.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_metadata (
                    run_id TEXT PRIMARY KEY,
                    alias TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    group_id TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    manual_verdict TEXT NOT NULL DEFAULT '',
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_run ON annotations(run_id, deleted)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_annotations_record ON annotations(run_id, record_id, group_id)")

    def upsert_run_metadata(
        self,
        *,
        run_id: str,
        alias: str | None = None,
        favorite: bool | None = None,
        hidden: bool | None = None,
    ) -> dict[str, Any]:
        now = _timestamp()
        existing = self.get_run_metadata(run_id)
        values = {
            "alias": existing.get("alias", "") if existing else "",
            "favorite": bool(existing.get("favorite", False)) if existing else False,
            "hidden": bool(existing.get("hidden", False)) if existing else False,
        }
        if alias is not None:
            values["alias"] = alias
        if favorite is not None:
            values["favorite"] = bool(favorite)
        if hidden is not None:
            values["hidden"] = bool(hidden)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_metadata (run_id, alias, favorite, hidden, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    alias = excluded.alias,
                    favorite = excluded.favorite,
                    hidden = excluded.hidden,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    str(values["alias"]),
                    1 if values["favorite"] else 0,
                    1 if values["hidden"] else 0,
                    now,
                    now,
                ),
            )
        return self.get_run_metadata(run_id) or {}

    def get_run_metadata(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM run_metadata WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "alias": row["alias"],
            "favorite": bool(row["favorite"]),
            "hidden": bool(row["hidden"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_run_metadata(self) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM run_metadata").fetchall()
        return {
            row["run_id"]: {
                "run_id": row["run_id"],
                "alias": row["alias"],
                "favorite": bool(row["favorite"]),
                "hidden": bool(row["hidden"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def create_annotation(
        self,
        *,
        run_id: str,
        record_id: str,
        group_id: str = "",
        note: str = "",
        status: str = "",
        tags: list[str] | tuple[str, ...] | None = None,
        manual_verdict: str = "",
    ) -> dict[str, Any]:
        now = _timestamp()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO annotations
                    (run_id, record_id, group_id, note, status, tags_json, manual_verdict, deleted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (run_id, record_id, group_id, note, status, _json_list(tags), manual_verdict, now, now),
            )
            annotation_id = int(cursor.lastrowid)
        return self.get_annotation(annotation_id, include_deleted=True) or {}

    def update_annotation(self, annotation_id: int, **updates: Any) -> dict[str, Any]:
        existing = self.get_annotation(annotation_id, include_deleted=True)
        if existing is None:
            raise KeyError(f"Unknown annotation id: {annotation_id}")
        values = {
            "note": str(updates.get("note", existing["note"]) or ""),
            "status": str(updates.get("status", existing["status"]) or ""),
            "tags_json": _json_list(updates.get("tags", existing["tags"])),
            "manual_verdict": str(updates.get("manual_verdict", existing["manual_verdict"]) or ""),
            "deleted": 1 if bool(updates.get("deleted", existing["deleted"])) else 0,
            "updated_at": _timestamp(),
            "id": annotation_id,
        }
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE annotations
                SET note = :note,
                    status = :status,
                    tags_json = :tags_json,
                    manual_verdict = :manual_verdict,
                    deleted = :deleted,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                values,
            )
        return self.get_annotation(annotation_id, include_deleted=True) or {}

    def delete_annotation(self, annotation_id: int) -> dict[str, Any]:
        return self.update_annotation(annotation_id, deleted=True)

    def get_annotation(self, annotation_id: int, *, include_deleted: bool = False) -> dict[str, Any] | None:
        query = "SELECT * FROM annotations WHERE id = ?"
        params: tuple[Any, ...] = (annotation_id,)
        if not include_deleted:
            query += " AND deleted = 0"
        with self._connect() as conn:
            row = conn.execute(query, params).fetchone()
        return _row_to_annotation(row) if row is not None else None

    def list_annotations(
        self,
        *,
        run_id: str | None = None,
        record_id: str | None = None,
        group_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if record_id is not None:
            clauses.append("record_id = ?")
            params.append(record_id)
        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(group_id)
        if not include_deleted:
            clauses.append("deleted = 0")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM annotations{where} ORDER BY updated_at DESC, id DESC", params).fetchall()
        return [_row_to_annotation(row) for row in rows]

