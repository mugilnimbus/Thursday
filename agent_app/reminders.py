from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .utils import utc_now


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    prompt: str
    recurrence: str
    time_of_day: str
    timezone_name: str
    date_value: str
    weekdays: list[str]
    enabled: bool
    created_at: str
    updated_at: str
    next_run_at: str
    last_run_at: str
    run_count: int
    last_status: str
    last_error: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "recurrence": self.recurrence,
            "time": self.time_of_day,
            "timezone": self.timezone_name,
            "date": self.date_value,
            "weekdays": self.weekdays,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "run_count": self.run_count,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }


class ReminderStore:
    def __init__(self, db_path: Path, default_timezone: str) -> None:
        self.db_path = db_path
        self.default_timezone = default_timezone
        ensure_reminder_schema(db_path)

    def create_reminder(
        self,
        title: str,
        prompt: str,
        recurrence: str = "daily",
        time_of_day: str = "09:00",
        timezone_name: str = "",
        date_value: str = "",
        weekdays: list[str] | None = None,
        enabled: bool = True,
    ) -> Reminder:
        normalized = normalize_schedule(
            recurrence=recurrence,
            time_of_day=time_of_day,
            timezone_name=timezone_name or self.default_timezone,
            date_value=date_value,
            weekdays=weekdays or [],
        )
        now = utc_now()
        reminder_id = uuid.uuid4().hex
        next_run_at = calculate_next_run_at(
            recurrence=normalized["recurrence"],
            time_of_day=normalized["time_of_day"],
            timezone_name=normalized["timezone_name"],
            date_value=normalized["date_value"],
            weekdays=normalized["weekdays"],
            after_utc=datetime.now(timezone.utc),
        )
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO reminders (
                    id, title, prompt, recurrence, time_of_day, timezone_name,
                    date_value, weekdays_json, enabled, created_at, updated_at,
                    next_run_at, last_run_at, run_count, last_status, last_error
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, 'scheduled', '')
                """,
                (
                    reminder_id,
                    title.strip(),
                    prompt.strip(),
                    normalized["recurrence"],
                    normalized["time_of_day"],
                    normalized["timezone_name"],
                    normalized["date_value"],
                    json.dumps(normalized["weekdays"], ensure_ascii=False),
                    1 if enabled else 0,
                    now,
                    now,
                    next_run_at,
                ),
            )
        return self.get_reminder(reminder_id)

    def get_reminder(self, reminder_id: str) -> Reminder:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            raise ValueError(f"Reminder not found: {reminder_id}")
        return reminder_from_row(row)

    def list_reminders(self, include_disabled: bool = True) -> list[Reminder]:
        ensure_reminder_schema(self.db_path)
        where = "" if include_disabled else "WHERE enabled = 1"
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM reminders {where} ORDER BY enabled DESC, next_run_at ASC, created_at ASC"
            ).fetchall()
        return [reminder_from_row(row) for row in rows]

    def delete_reminder(self, reminder_id: str) -> bool:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            return cursor.rowcount > 0

    def update_reminder(self, reminder_id: str, updates: dict[str, Any]) -> Reminder:
        current = self.get_reminder(reminder_id)
        title = str(updates.get("title", current.title)).strip()
        prompt = str(updates.get("prompt", current.prompt)).strip()
        recurrence = str(updates.get("recurrence", current.recurrence))
        time_of_day = str(updates.get("time", updates.get("time_of_day", current.time_of_day)))
        timezone_name = str(updates.get("timezone", updates.get("timezone_name", current.timezone_name)))
        date_value = str(updates.get("date", updates.get("date_value", current.date_value)))
        weekdays = updates.get("weekdays", current.weekdays)
        enabled = bool(updates.get("enabled", current.enabled))
        normalized = normalize_schedule(recurrence, time_of_day, timezone_name, date_value, list(weekdays or []))
        next_run_at = calculate_next_run_at(
            recurrence=normalized["recurrence"],
            time_of_day=normalized["time_of_day"],
            timezone_name=normalized["timezone_name"],
            date_value=normalized["date_value"],
            weekdays=normalized["weekdays"],
            after_utc=datetime.now(timezone.utc),
        )
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                UPDATE reminders
                SET title = ?, prompt = ?, recurrence = ?, time_of_day = ?,
                    timezone_name = ?, date_value = ?, weekdays_json = ?,
                    enabled = ?, updated_at = ?, next_run_at = ?, last_status = 'scheduled', last_error = ''
                WHERE id = ?
                """,
                (
                    title,
                    prompt,
                    normalized["recurrence"],
                    normalized["time_of_day"],
                    normalized["timezone_name"],
                    normalized["date_value"],
                    json.dumps(normalized["weekdays"], ensure_ascii=False),
                    1 if enabled else 0,
                    utc_now(),
                    next_run_at,
                    reminder_id,
                ),
            )
        return self.get_reminder(reminder_id)

    def due_reminders(self, now_utc: datetime, limit: int = 5) -> list[Reminder]:
        now_text = now_utc.astimezone(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM reminders
                WHERE enabled = 1
                  AND next_run_at != ''
                  AND next_run_at <= ?
                  AND last_status != 'running'
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (now_text, max(1, limit)),
            ).fetchall()
        return [reminder_from_row(row) for row in rows]

    def mark_running(self, reminder_id: str) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                "UPDATE reminders SET last_status = 'running', updated_at = ? WHERE id = ?",
                (utc_now(), reminder_id),
            )

    def mark_finished(self, reminder_id: str, ok: bool, error: str = "") -> Reminder:
        reminder = self.get_reminder(reminder_id)
        now_dt = datetime.now(timezone.utc)
        if reminder.recurrence == "once":
            next_run_at = ""
            enabled = 0
        else:
            next_run_at = calculate_next_run_at(
                recurrence=reminder.recurrence,
                time_of_day=reminder.time_of_day,
                timezone_name=reminder.timezone_name,
                date_value=reminder.date_value,
                weekdays=reminder.weekdays,
                after_utc=now_dt,
            )
            enabled = 1 if reminder.enabled else 0
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                UPDATE reminders
                SET last_status = ?, last_error = ?, last_run_at = ?, updated_at = ?,
                    next_run_at = ?, enabled = ?, run_count = run_count + 1
                WHERE id = ?
                """,
                (
                    "completed" if ok else "error",
                    error,
                    now_dt.isoformat(),
                    now_dt.isoformat(),
                    next_run_at,
                    enabled,
                    reminder_id,
                ),
            )
        return self.get_reminder(reminder_id)


def ensure_reminder_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                recurrence TEXT NOT NULL,
                time_of_day TEXT NOT NULL,
                timezone_name TEXT NOT NULL,
                date_value TEXT NOT NULL,
                weekdays_json TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                next_run_at TEXT NOT NULL,
                last_run_at TEXT NOT NULL,
                run_count INTEGER NOT NULL,
                last_status TEXT NOT NULL,
                last_error TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_next_run_at ON reminders(next_run_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reminders_enabled ON reminders(enabled)")


def reminder_from_row(row: sqlite3.Row) -> Reminder:
    try:
        weekdays = json.loads(row["weekdays_json"] or "[]")
    except json.JSONDecodeError:
        weekdays = []
    return Reminder(
        id=str(row["id"]),
        title=str(row["title"]),
        prompt=str(row["prompt"]),
        recurrence=str(row["recurrence"]),
        time_of_day=str(row["time_of_day"]),
        timezone_name=str(row["timezone_name"]),
        date_value=str(row["date_value"]),
        weekdays=[str(item).lower() for item in weekdays if str(item).strip()],
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        next_run_at=str(row["next_run_at"]),
        last_run_at=str(row["last_run_at"]),
        run_count=int(row["run_count"] or 0),
        last_status=str(row["last_status"]),
        last_error=str(row["last_error"]),
    )


def normalize_schedule(
    recurrence: str,
    time_of_day: str,
    timezone_name: str,
    date_value: str,
    weekdays: list[str],
) -> dict[str, Any]:
    recurrence = recurrence.strip().lower()
    if recurrence not in {"daily", "weekly", "once"}:
        raise ValueError("recurrence must be daily, weekly, or once")
    parsed_time = parse_time_of_day(time_of_day)
    zone = parse_timezone(timezone_name)
    normalized_weekdays = [normalize_weekday(item) for item in weekdays if str(item).strip()]
    if recurrence == "weekly" and not normalized_weekdays:
        raise ValueError("weekly reminders require at least one weekday")
    normalized_date = ""
    if recurrence == "once":
        normalized_date = parse_date_value(date_value or datetime.now(zone).date().isoformat()).isoformat()
    return {
        "recurrence": recurrence,
        "time_of_day": parsed_time.strftime("%H:%M"),
        "timezone_name": zone.key,
        "date_value": normalized_date,
        "weekdays": normalized_weekdays,
    }


def calculate_next_run_at(
    recurrence: str,
    time_of_day: str,
    timezone_name: str,
    date_value: str,
    weekdays: list[str],
    after_utc: datetime,
) -> str:
    zone = parse_timezone(timezone_name)
    local_now = after_utc.astimezone(zone)
    local_time = parse_time_of_day(time_of_day)
    if recurrence == "once":
        local_date = parse_date_value(date_value or local_now.date().isoformat())
        candidate = datetime.combine(local_date, local_time, tzinfo=zone)
        if candidate <= local_now:
            candidate = local_now + timedelta(seconds=5)
        return candidate.astimezone(timezone.utc).isoformat()
    if recurrence == "daily":
        candidate = datetime.combine(local_now.date(), local_time, tzinfo=zone)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc).isoformat()
    weekday_numbers = sorted(WEEKDAYS[item] for item in weekdays)
    for offset in range(0, 8):
        candidate_date = local_now.date() + timedelta(days=offset)
        if candidate_date.weekday() not in weekday_numbers:
            continue
        candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
        if candidate > local_now:
            return candidate.astimezone(timezone.utc).isoformat()
    raise ValueError("Could not calculate next weekly reminder run")


def parse_timezone(value: str) -> ZoneInfo:
    name = (value or "Europe/Dublin").strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc


def parse_time_of_day(value: str) -> time:
    raw = (value or "").strip().lower().replace(".", "")
    suffix = ""
    if raw.endswith("am") or raw.endswith("pm"):
        suffix = raw[-2:]
        raw = raw[:-2].strip()
    parts = raw.split(":")
    if len(parts) == 1:
        hour = int(parts[0])
        minute = 0
    elif len(parts) == 2:
        hour = int(parts[0])
        minute = int(parts[1])
    else:
        raise ValueError("time must use HH:MM, HH, or h:mmam/pm format")
    if suffix == "pm" and hour != 12:
        hour += 12
    if suffix == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time is out of range")
    return time(hour=hour, minute=minute)


def parse_date_value(value: str) -> date:
    return date.fromisoformat(value.strip())


def normalize_weekday(value: str) -> str:
    lowered = value.strip().lower()
    if lowered not in WEEKDAYS:
        raise ValueError(f"Unknown weekday: {value}")
    return lowered
