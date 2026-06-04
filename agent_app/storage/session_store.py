from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..runtime.models import AgentSettings, Event, Session
from ..utils import utc_now


def ensure_session_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at)")


class SessionStore:
    def __init__(self, db_path: Path, event_history_limit: int) -> None:
        self.db_path = db_path
        self.event_history_limit = event_history_limit
        ensure_session_schema(db_path)

    def load_all(self) -> dict[str, Session]:
        ensure_session_schema(self.db_path)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT payload_json FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        sessions: dict[str, Session] = {}
        for row in rows:
            try:
                session = self._deserialize(json.loads(row["payload_json"]))
            except Exception:
                continue
            sessions[session.id] = session
        return sessions

    def save(self, session: Session) -> None:
        ensure_session_schema(self.db_path)
        payload = self._serialize(session)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO sessions (id, title, created_at, updated_at, status, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    updated_at = excluded.updated_at,
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    session.id,
                    session.title,
                    session.created_at,
                    session.updated_at,
                    session.status,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def delete(self, session_id: str) -> bool:
        ensure_session_schema(self.db_path)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def clear_all(self) -> int:
        ensure_session_schema(self.db_path)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            conn.execute("DELETE FROM sessions")
            return int(total)

    def _serialize(self, session: Session) -> dict[str, Any]:
        with session.lock:
            return {
                "id": session.id,
                "title": session.title,
                "settings": asdict(session.settings),
                "event_history_limit": session.event_history_limit,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "status": session.status,
                "messages": session.messages,
                "message_backup": session.message_backup,
                "visible_messages": session.visible_messages,
                "events": [asdict(event) for event in session.events],
                "summary": session.summary,
                "tool_counts": session.tool_counts,
                "modified_files": session.modified_files,
                "recent_errors": session.recent_errors,
                "current_goal": session.current_goal,
                "token_estimate": session.token_estimate,
                "last_response_id": session.last_response_id,
                "response_chain_valid": session.response_chain_valid,
                "response_anchor_message_count": session.response_anchor_message_count,
                "response_chain_model": session.response_chain_model,
                "response_chain_endpoint": session.response_chain_endpoint,
                "response_chain_invalid_reason": session.response_chain_invalid_reason,
            }

    def _deserialize(self, payload: dict[str, Any]) -> Session:
        settings_payload = payload.get("settings") or {}
        allowed_settings = AgentSettings.__dataclass_fields__.keys()
        settings = AgentSettings(**{key: value for key, value in settings_payload.items() if key in allowed_settings})

        events: list[Event] = []
        for event_payload in payload.get("events") or []:
            if not isinstance(event_payload, dict):
                continue
            events.append(
                Event(
                    type=str(event_payload.get("type") or "event"),
                    message=str(event_payload.get("message") or ""),
                    timestamp=str(event_payload.get("timestamp") or utc_now()),
                    data=event_payload.get("data") if isinstance(event_payload.get("data"), dict) else {},
                )
            )

        status = str(payload.get("status") or "idle")
        if status == "running":
            status = "idle"
            events.append(Event("restored", "Session restored after server restart; previous run was interrupted"))

        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        message_backup = payload.get("message_backup") if isinstance(payload.get("message_backup"), list) else []
        if not message_backup:
            message_backup = list(messages)

        return Session(
            id=str(payload.get("id") or ""),
            title=str(payload.get("title") or "Restored session"),
            settings=settings,
            event_history_limit=int(payload.get("event_history_limit") or self.event_history_limit),
            created_at=str(payload.get("created_at") or utc_now()),
            updated_at=str(payload.get("updated_at") or utc_now()),
            status=status,
            messages=messages,
            message_backup=message_backup,
            visible_messages=payload.get("visible_messages") if isinstance(payload.get("visible_messages"), list) else [],
            events=events[-self.event_history_limit :],
            summary=str(payload.get("summary") or ""),
            tool_counts=payload.get("tool_counts") if isinstance(payload.get("tool_counts"), dict) else {},
            modified_files=payload.get("modified_files") if isinstance(payload.get("modified_files"), list) else [],
            recent_errors=payload.get("recent_errors") if isinstance(payload.get("recent_errors"), list) else [],
            current_goal=str(payload.get("current_goal") or ""),
            token_estimate=int(payload.get("token_estimate") or 0),
            last_response_id=str(payload.get("last_response_id") or ""),
            response_chain_valid=bool(payload.get("response_chain_valid", False)),
            response_anchor_message_count=int(payload.get("response_anchor_message_count") or 0),
            response_chain_model=str(payload.get("response_chain_model") or ""),
            response_chain_endpoint=str(payload.get("response_chain_endpoint") or ""),
            response_chain_invalid_reason=str(payload.get("response_chain_invalid_reason") or ""),
        )

