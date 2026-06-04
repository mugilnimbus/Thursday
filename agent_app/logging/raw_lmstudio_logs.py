from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .sqlite_handler import SQLiteLogHandler


def ensure_raw_lmstudio_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lmstudio_raw_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                request_id TEXT,
                kind TEXT,
                attempt INTEGER,
                compatibility_mode INTEGER,
                url TEXT,
                status_code INTEGER,
                elapsed_ms REAL,
                request_json TEXT,
                response_json TEXT,
                response_text TEXT,
                error_json TEXT,
                raw_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lmstudio_raw_created_at ON lmstudio_raw_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lmstudio_raw_request_id ON lmstudio_raw_logs(request_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lmstudio_endpoint_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                request_id TEXT,
                kind TEXT,
                attempt INTEGER,
                compatibility_mode INTEGER,
                method TEXT NOT NULL DEFAULT 'POST',
                url TEXT,
                model TEXT,
                status_code INTEGER,
                elapsed_ms REAL,
                request_payload_json TEXT NOT NULL,
                request_headers_json TEXT,
                request_message_count INTEGER,
                request_content_chars INTEGER,
                response_headers_json TEXT,
                response_body_text TEXT,
                response_body_json TEXT,
                response_json_error TEXT,
                finish_reason TEXT,
                response_content_text TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                error_json TEXT,
                raw_record_json TEXT NOT NULL,
                legacy_raw_id INTEGER UNIQUE
            )
            """
        )
        ensure_column(conn, "lmstudio_endpoint_logs", "request_headers_json", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lmstudio_endpoint_created_at ON lmstudio_endpoint_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lmstudio_endpoint_request_id ON lmstudio_endpoint_logs(request_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lmstudio_endpoint_status_code ON lmstudio_endpoint_logs(status_code)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_lmstudio_raw_lines (
                path TEXT NOT NULL,
                line_no INTEGER NOT NULL,
                PRIMARY KEY (path, line_no)
            )
            """
        )
        migrate_raw_logs_to_endpoint_logs(conn)


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def insert_raw_lmstudio_log(db_path: Path, record: dict[str, Any]) -> None:
    ensure_raw_lmstudio_schema(db_path)
    response = record.get("response") or {}
    request_payload = request_payload_from_record(record)
    with SQLiteLogHandler._lock:
        with sqlite3.connect(db_path, timeout=10) as conn:
            cursor = conn.execute(
                """
                INSERT INTO lmstudio_raw_logs (
                    created_at, request_id, kind, attempt, compatibility_mode, url,
                    status_code, elapsed_ms, request_json, response_json,
                    response_text, error_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                    str(record.get("request_id") or ""),
                    str(record.get("kind") or ""),
                    int(record.get("attempt") or 0),
                    1 if record.get("compatibility_mode") else 0,
                    str(record.get("url") or ""),
                    response.get("status_code"),
                    record.get("elapsed_ms"),
                    json.dumps(request_payload, ensure_ascii=False),
                    json.dumps(response.get("json"), ensure_ascii=False),
                    str(response.get("text") or ""),
                    json.dumps(record.get("error"), ensure_ascii=False),
                    json.dumps(record, ensure_ascii=False),
                ),
            )
            insert_endpoint_log(conn, record, legacy_raw_id=int(cursor.lastrowid))


def request_payload_from_record(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request") or {}
    if isinstance(request, dict) and isinstance(request.get("json"), dict):
        return request["json"]
    return request if isinstance(request, dict) else {}


def request_headers_from_record(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request") or {}
    headers = request.get("headers") if isinstance(request, dict) else {}
    return headers if isinstance(headers, dict) else {}


def response_json_from_record(record: dict[str, Any]) -> Any:
    response = record.get("response") or {}
    if isinstance(response, dict) and "json" in response:
        return response.get("json")
    return None


def response_message_from_record(record: dict[str, Any]) -> dict[str, Any]:
    response_json = response_json_from_record(record)
    if not isinstance(response_json, dict):
        return {}
    choices = response_json.get("choices") or []
    if not choices:
        return {}
    message = choices[0].get("message") or {}
    return message if isinstance(message, dict) else {}


def first_choice_from_record(record: dict[str, Any]) -> dict[str, Any]:
    response_json = response_json_from_record(record)
    if not isinstance(response_json, dict):
        return {}
    choices = response_json.get("choices") or []
    return choices[0] if choices and isinstance(choices[0], dict) else {}


def usage_from_record(record: dict[str, Any]) -> dict[str, Any]:
    response_json = response_json_from_record(record)
    usage = response_json.get("usage") if isinstance(response_json, dict) else {}
    return usage if isinstance(usage, dict) else {}


def request_content_chars(payload: dict[str, Any]) -> int:
    messages = payload.get("messages") or []
    total = 0
    for message in messages:
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                total += len(json.dumps(content, ensure_ascii=False))
    return total


def insert_endpoint_log(conn: sqlite3.Connection, record: dict[str, Any], legacy_raw_id: int | None = None) -> None:
    payload = request_payload_from_record(record)
    response = record.get("response") or {}
    response_json = response.get("json") if isinstance(response, dict) else None
    message = response_message_from_record(record)
    choice = first_choice_from_record(record)
    usage = usage_from_record(record)
    messages = payload.get("messages") or []
    conn.execute(
        """
        INSERT OR IGNORE INTO lmstudio_endpoint_logs (
            created_at, request_id, kind, attempt, compatibility_mode, method, url,
            model, status_code, elapsed_ms, request_payload_json,
            request_headers_json, request_message_count, request_content_chars, response_headers_json,
            response_body_text, response_body_json, response_json_error,
            finish_reason, response_content_text, prompt_tokens, completion_tokens,
            total_tokens, error_json, raw_record_json, legacy_raw_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(record.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            str(record.get("request_id") or ""),
            str(record.get("kind") or ""),
            int(record.get("attempt") or 0),
            1 if record.get("compatibility_mode") else 0,
            str(record.get("method") or "POST"),
            str(record.get("url") or ""),
            str(payload.get("model") or ""),
            response.get("status_code") if isinstance(response, dict) else None,
            record.get("elapsed_ms"),
            json.dumps(payload, ensure_ascii=False),
            json.dumps(request_headers_from_record(record), ensure_ascii=False),
            len(messages) if isinstance(messages, list) else 0,
            request_content_chars(payload),
            json.dumps(response.get("headers") or {}, ensure_ascii=False) if isinstance(response, dict) else "{}",
            str(response.get("text") or "") if isinstance(response, dict) else "",
            json.dumps(response_json, ensure_ascii=False),
            str(response.get("json_error") or "") if isinstance(response, dict) else "",
            str(choice.get("finish_reason") or ""),
            str(message.get("content") or ""),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            json.dumps(record.get("error"), ensure_ascii=False),
            json.dumps(record, ensure_ascii=False),
            legacy_raw_id,
        ),
    )


def migrate_raw_logs_to_endpoint_logs(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT raw.id, raw.raw_json
        FROM lmstudio_raw_logs raw
        LEFT JOIN lmstudio_endpoint_logs endpoint ON endpoint.legacy_raw_id = raw.id
        WHERE endpoint.id IS NULL
        ORDER BY raw.id
        """
    ).fetchall()
    for raw_id, raw_json in rows:
        try:
            record = json.loads(raw_json)
        except (TypeError, json.JSONDecodeError):
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": "",
                "kind": "lmstudio.chat.completions",
                "attempt": 0,
                "compatibility_mode": False,
                "url": "",
                "request": {"json": {}},
                "error": {"type": "JSONDecodeError", "message": "Could not decode stored raw_json"},
                "raw": raw_json,
            }
        insert_endpoint_log(conn, record, legacy_raw_id=int(raw_id))


def fetch_raw_lmstudio_logs(db_path: Path, limit: int = 40) -> dict[str, Any]:
    ensure_raw_lmstudio_schema(db_path)
    limit = max(1, min(limit, 200))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) FROM lmstudio_endpoint_logs").fetchone()[0]
        rows = conn.execute(
            """
            SELECT *
            FROM lmstudio_endpoint_logs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    entries: list[dict[str, Any]] = []
    for row in rows:
        request_payload = json.loads(row["request_payload_json"] or "{}")
        response_json = json.loads(row["response_body_json"] or "null")
        request_headers = json.loads(row["request_headers_json"] or "{}")
        response_headers = json.loads(row["response_headers_json"] or "{}")
        error = json.loads(row["error_json"] or "null")
        entry = {
            "id": row["id"],
            "_line": row["id"],
            "timestamp": row["created_at"],
            "request_id": row["request_id"],
            "kind": row["kind"],
            "attempt": row["attempt"],
            "compatibility_mode": bool(row["compatibility_mode"]),
            "method": row["method"],
            "url": row["url"],
            "model": row["model"],
            "status_code": row["status_code"],
            "elapsed_ms": row["elapsed_ms"],
            "request_message_count": row["request_message_count"],
            "request_content_chars": row["request_content_chars"],
            "finish_reason": row["finish_reason"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "total_tokens": row["total_tokens"],
            "request": {"headers": request_headers, "json": request_payload},
            "response": {
                "status_code": row["status_code"],
                "headers": response_headers,
                "text": row["response_body_text"] or "",
                "json": response_json,
                "json_error": row["response_json_error"] or "",
            },
            "error": error,
            "stored_record": json.loads(row["raw_record_json"] or "{}"),
        }
        entries.append(entry)
    entries.reverse()
    return {"file": str(db_path), "entries": entries, "line_count": total, "storage": "sqlite", "table": "lmstudio_endpoint_logs"}


def import_raw_lmstudio_jsonl(db_path: Path, jsonl_path: Path) -> int:
    if not jsonl_path.exists():
        ensure_raw_lmstudio_schema(db_path)
        return 0
    ensure_raw_lmstudio_schema(db_path)
    imported = 0
    source_path = str(jsonl_path)
    with SQLiteLogHandler._lock:
        with sqlite3.connect(db_path, timeout=30) as conn:
            with jsonl_path.open("r", encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, start=1):
                    raw = line.strip()
                    if not raw:
                        continue
                    exists = conn.execute(
                        "SELECT 1 FROM imported_lmstudio_raw_lines WHERE path = ? AND line_no = ?",
                        (source_path, line_no),
                    ).fetchone()
                    if exists:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError:
                        record = {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "request_id": "",
                            "kind": "lmstudio.chat.completions",
                            "attempt": 0,
                            "compatibility_mode": False,
                            "url": "",
                            "error": {"type": "JSONDecodeError", "message": "Could not parse legacy raw log line"},
                            "raw": raw,
                        }
                    response = record.get("response") or {}
                    request_payload = request_payload_from_record(record)
                    conn.execute(
                        """
                        INSERT INTO lmstudio_raw_logs (
                            created_at, request_id, kind, attempt, compatibility_mode, url,
                            status_code, elapsed_ms, request_json, response_json,
                            response_text, error_json, raw_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(record.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                            str(record.get("request_id") or ""),
                            str(record.get("kind") or ""),
                            int(record.get("attempt") or 0),
                            1 if record.get("compatibility_mode") else 0,
                            str(record.get("url") or ""),
                            response.get("status_code"),
                            record.get("elapsed_ms"),
                            json.dumps(request_payload, ensure_ascii=False),
                            json.dumps(response.get("json"), ensure_ascii=False),
                            str(response.get("text") or ""),
                            json.dumps(record.get("error"), ensure_ascii=False),
                            json.dumps(record, ensure_ascii=False),
                        ),
                    )
                    legacy_raw_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    insert_endpoint_log(conn, record, legacy_raw_id=int(legacy_raw_id))
                    conn.execute(
                        "INSERT INTO imported_lmstudio_raw_lines (path, line_no) VALUES (?, ?)",
                        (source_path, line_no),
                    )
                    imported += 1
    return imported
