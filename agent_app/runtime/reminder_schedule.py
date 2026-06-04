from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def normalize_schedule(
    recurrence: str,
    time_of_day: str,
    timezone_name: str,
    date_value: str,
    weekdays: list[str],
    interval_minutes: int = 0,
    day_of_month: int = 0,
) -> dict[str, Any]:
    recurrence = normalize_recurrence(recurrence)
    zone = parse_timezone(timezone_name)
    if recurrence == "hourly":
        parsed_time = time(hour=0, minute=parse_hourly_minute(time_of_day))
    elif recurrence == "every_minutes":
        parsed_time = time(hour=0, minute=0)
    else:
        parsed_time = parse_time_of_day(time_of_day or "09:00")
    normalized_weekdays = [normalize_weekday(item) for item in weekdays if str(item).strip()]
    if recurrence == "weekly" and not normalized_weekdays:
        raise ValueError("weekly reminders require at least one weekday")
    normalized_interval = max(0, int(interval_minutes or 0))
    if recurrence == "every_minutes" and normalized_interval < 1:
        raise ValueError("every_minutes reminders require interval_minutes >= 1")
    normalized_day = max(0, min(31, int(day_of_month or 0)))
    if recurrence == "monthly" and normalized_day < 1:
        raise ValueError("monthly reminders require day_of_month between 1 and 31")
    normalized_date = ""
    if recurrence == "once":
        normalized_date = parse_date_value(date_value or datetime.now(zone).date().isoformat()).isoformat()
    return {
        "recurrence": recurrence,
        "time_of_day": parsed_time.strftime("%H:%M"),
        "timezone_name": zone.key,
        "date_value": normalized_date,
        "weekdays": normalized_weekdays,
        "interval_minutes": normalized_interval,
        "day_of_month": normalized_day,
    }


def calculate_next_run_at(
    recurrence: str,
    time_of_day: str,
    timezone_name: str,
    date_value: str,
    weekdays: list[str],
    after_utc: datetime,
    interval_minutes: int = 0,
    day_of_month: int = 0,
) -> str:
    recurrence = normalize_recurrence(recurrence)
    zone = parse_timezone(timezone_name)
    local_now = after_utc.astimezone(zone)
    local_time = parse_time_of_day(time_of_day)
    if recurrence == "every_minutes":
        return (after_utc.astimezone(timezone.utc) + timedelta(minutes=max(1, int(interval_minutes)))).isoformat()
    if recurrence == "hourly":
        candidate = local_now.replace(minute=local_time.minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(hours=1)
        return candidate.astimezone(timezone.utc).isoformat()
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
    if recurrence == "monthly":
        target_day = max(1, min(31, int(day_of_month or 1)))
        year = local_now.year
        month = local_now.month
        for _ in range(15):
            day = min(target_day, calendar.monthrange(year, month)[1])
            candidate = datetime.combine(date(year, month, day), local_time, tzinfo=zone)
            if candidate > local_now:
                return candidate.astimezone(timezone.utc).isoformat()
            month += 1
            if month > 12:
                month = 1
                year += 1
        raise ValueError("Could not calculate next monthly reminder run")
    weekday_numbers = sorted(WEEKDAYS[item] for item in weekdays)
    for offset in range(0, 8):
        candidate_date = local_now.date() + timedelta(days=offset)
        if candidate_date.weekday() not in weekday_numbers:
            continue
        candidate = datetime.combine(candidate_date, local_time, tzinfo=zone)
        if candidate > local_now:
            return candidate.astimezone(timezone.utc).isoformat()
    raise ValueError("Could not calculate next weekly reminder run")


def normalize_recurrence(value: str) -> str:
    lowered = (value or "daily").strip().lower().replace("-", "_")
    aliases = {
        "minute": "every_minutes",
        "minutes": "every_minutes",
        "every_minute": "every_minutes",
        "interval": "every_minutes",
        "interval_minutes": "every_minutes",
        "hour": "hourly",
        "every_hour": "hourly",
        "day": "daily",
        "every_day": "daily",
        "week": "weekly",
        "every_week": "weekly",
        "month": "monthly",
        "every_month": "monthly",
        "one_time": "once",
        "one_time_only": "once",
    }
    recurrence = aliases.get(lowered, lowered)
    allowed = {"once", "every_minutes", "hourly", "daily", "weekly", "monthly"}
    if recurrence not in allowed:
        raise ValueError("schedule must be one of: once, every_minutes, hourly, daily, weekly, monthly")
    return recurrence


def parse_timezone(value: str) -> ZoneInfo:
    name = (value or "UTC").strip()
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


def parse_hourly_minute(value: str) -> int:
    raw = (value or "").strip().lower()
    if raw == "":
        return 0
    if raw.isdigit():
        minute = int(raw)
        if 0 <= minute <= 59:
            return minute
    return parse_time_of_day(raw).minute


def parse_date_value(value: str) -> date:
    return date.fromisoformat(value.strip())


def normalize_weekday(value: str) -> str:
    lowered = value.strip().lower()
    if lowered not in WEEKDAYS:
        raise ValueError(f"Unknown weekday: {value}")
    return lowered
