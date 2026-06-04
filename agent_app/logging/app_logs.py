from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .raw_lmstudio_logs import ensure_raw_lmstudio_schema
from .sqlite_handler import SQLiteLogHandler


def fetch_recent_logs(db_path: Path, limit: int = 120, source: str = "") -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    limit = max(1, min(limit, 500))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if source:
            rows = conn.execute(
                """
                SELECT id, created_at, level, logger, source, message
                FROM logs
                WHERE source = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, created_at, level, logger, source, message
                FROM logs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def clear_log_tables(db_path: Path) -> dict[str, Any]:
    ensure_raw_lmstudio_schema(db_path)
    SQLiteLogHandler(db_path, "maintenance")._ensure_schema()
    tables = [
        "logs",
        "lmstudio_raw_logs",
        "lmstudio_endpoint_logs",
        "imported_lmstudio_raw_lines",
    ]
    deleted: dict[str, int] = {}
    with SQLiteLogHandler._lock:
        with sqlite3.connect(db_path, timeout=30) as conn:
            for table in tables:
                try:
                    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    conn.execute(f"DELETE FROM {table}")
                    deleted[table] = int(total)
                except sqlite3.Error:
                    deleted[table] = 0
    checkpoint = "skipped"
    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            checkpoint = "ok"
    except sqlite3.Error as exc:
        checkpoint = f"skipped: {exc}"
    return {"ok": True, "database": str(db_path), "deleted": deleted, "checkpoint": checkpoint}
