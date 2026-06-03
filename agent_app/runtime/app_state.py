from __future__ import annotations

import logging
import shutil
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from ..config import AppConfig
from ..models import AgentSettings, Session
from ..logging_store import clear_log_tables
from ..orchestration import AgentOrchestrator
from ..preferences import DashboardPreferences, PreferenceStore
from ..preferences import REQUIRED_TOOLS
from ..reminders import Reminder, ReminderStore
from ..storage import ImageStore, SessionStore
from ..workspace import docker_workspace_status, reset_docker_workspace


REMINDER_SESSION_TITLE = "Scheduled reminders"


class AppState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = logging.getLogger("agent.server")
        self.stop_event = threading.Event()
        self.session_store = SessionStore(config.log_db_file, config.event_history_limit)
        self.image_store = ImageStore(config)
        self.sessions: dict[str, Session] = self.session_store.load_all()
        self.lock = threading.RLock()
        self.reminder_store = ReminderStore(config.log_db_file, config.reminder_timezone)
        self.orchestrator = AgentOrchestrator(config, reminder_store=self.reminder_store)
        self.orchestrator.session_update_callback = self.session_store.save
        self.preference_store = PreferenceStore(
            config.preferences_file,
            config,
            self.orchestrator.tools.names(),
        )
        self.preferences = self.preference_store.load()
        self.preference_store.save(self.preferences)
        for session in self.sessions.values():
            self.session_store.save(session)
        self.reminder_thread = threading.Thread(target=self.reminder_loop, name="ThursdayReminderScheduler", daemon=True)
        self.reminder_thread.start()

    def preferences_public(self) -> dict[str, object]:
        with self.lock:
            return self.preferences.to_public(self.config, self.orchestrator.tools.names())

    def lmstudio_status(self, endpoint: str | None = None, model: str | None = None) -> dict[str, object]:
        resolved_endpoint = endpoint or self.config.lmstudio_endpoint
        selected_model = model or self.preferences.settings.model or self.config.lmstudio_model
        status = self.orchestrator.llm.status(resolved_endpoint)
        context = self.orchestrator.llm.model_context_window(
            resolved_endpoint,
            selected_model,
            self.preferences.settings.context_window or self.config.default_context_window,
        )
        return {**status, "selected_model": selected_model, "selected_model_context": context}

    def tools_public(self) -> dict[str, object]:
        return {
            "tools": self.orchestrator.tools.definitions(self.preferences.enabled_tools),
            "all_tools": self.orchestrator.tools.definitions(),
            "tool_catalog": self.orchestrator.tools.catalog(self.preferences.enabled_tools),
            "all_tool_catalog": self.orchestrator.tools.catalog(),
            "enabled_tools": self.preferences.enabled_tools,
            "required_tools": [name for name in REQUIRED_TOOLS if name in self.orchestrator.tools.names()],
            "workspace": self.orchestrator.tools.workspace_label(),
            "tool_api": {
                "call": {"input": "tool-specific fields", "meta": "optional caller metadata"},
                "result": {"ok": "boolean", "tool": "tool name", "output": "successful data", "error": "failure object or null", "meta": "execution metadata"},
            },
        }

    def run_chat_turn(
        self,
        session: Session,
        message: str,
        settings: dict[str, object],
        images: list[dict[str, object]],
    ) -> None:
        settings = self.with_model_context_settings(settings)
        self.orchestrator.run_turn(session, message, settings, self.image_store.normalize_payload_images(images))

    def save_uploaded_image(self, data: bytes, name: str, mime_type: str) -> dict[str, object]:
        return self.image_store.save_uploaded_image(data, name, mime_type).public()

    def update_preferences(self, payload: dict[str, object]) -> dict[str, object]:
        with self.lock:
            settings = AgentSettings.from_config(self.config)
            for key, value in (payload.get("settings") or {}).items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            self.apply_model_context_to_agent_settings(settings)
            enabled_tools = [
                str(name)
                for name in payload.get("enabled_tools", self.preferences.enabled_tools)
                if str(name) in self.orchestrator.tools.names()
            ]
            enabled_tools = self.preference_store.with_required_tools(enabled_tools)
            settings.enabled_tools = enabled_tools
            theme_payload = payload.get("theme") if isinstance(payload.get("theme"), dict) else {}
            theme_color = self.preference_store.normalize_theme_color(theme_payload.get("accent_color"))
            self.preferences = DashboardPreferences(settings=settings, enabled_tools=enabled_tools, theme_color=theme_color)
            self.preference_store.save(self.preferences)
            return self.preferences_public()

    def restore_preferences(self) -> dict[str, object]:
        with self.lock:
            self.preferences = self.preference_store.restore_defaults()
            self.preferences.settings.enabled_tools = list(self.preferences.enabled_tools)
            return self.preferences_public()

    def create_session(self) -> Session:
        settings = AgentSettings(**asdict(self.preferences.settings))
        settings.enabled_tools = list(self.preferences.enabled_tools)
        session = Session(
            id=uuid.uuid4().hex,
            title="New session",
            settings=settings,
            event_history_limit=self.config.event_history_limit,
        )
        with self.lock:
            self.sessions[session.id] = session
            self.session_store.save(session)
        return session

    def get_session(self, session_id: str | None) -> Session:
        with self.lock:
            if session_id and session_id in self.sessions:
                return self.sessions[session_id]
            return self.create_session()

    def delete_session(self, session_id: str) -> bool:
        with self.lock:
            removed = self.sessions.pop(session_id, None) is not None
            deleted = self.session_store.delete(session_id)
            return removed or deleted

    def clear_sessions(self) -> dict[str, object]:
        with self.lock:
            running = self.running_session_titles()
            if running:
                return {
                    "ok": False,
                    "error": "Cannot clear sessions while an agent turn is running.",
                    "running_sessions": running,
                }
            count = len(self.sessions)
            self.sessions.clear()
            stored = self.session_store.clear_all()
            return {"ok": True, "cleared": max(count, stored)}

    def clear_logs(self) -> dict[str, object]:
        result = clear_log_tables(self.config.log_db_file)
        artifacts = {
            "visual_checks": self.clear_directory_contents(self.config.visual_check_dir),
            "server_log": self.truncate_file(self.config.log_dir / "server.log"),
            "server_error_log": self.truncate_file(self.config.log_dir / "server.err.log"),
            "legacy_raw_log": self.truncate_file(self.config.lmstudio_raw_log_file),
        }
        return {**result, "artifacts": artifacts}

    def reminders_public(self, include_disabled: bool = True) -> dict[str, object]:
        reminders = [reminder.public() for reminder in self.reminder_store.list_reminders(include_disabled=include_disabled)]
        return {
            "reminders": reminders,
            "count": len(reminders),
            "timezone": self.config.reminder_timezone,
            "poll_seconds": self.config.reminder_poll_seconds,
        }

    def workspace_status(self) -> dict[str, object]:
        return docker_workspace_status(self.config)

    def reset_workspace(self) -> dict[str, object]:
        with self.lock:
            running_sessions = self.running_session_titles()
            if running_sessions:
                return {
                    "ok": False,
                    "error": "Cannot reset the Docker workspace while an agent turn is running.",
                    "running_sessions": running_sessions,
                    "status": docker_workspace_status(self.config),
                }
        return reset_docker_workspace(self.config)

    def running_session_titles(self) -> list[str]:
        return [
            session.title or session.id
            for session in self.sessions.values()
            if session.status == "running"
        ]

    def clear_directory_contents(self, path) -> dict[str, object]:
        path.mkdir(parents=True, exist_ok=True)
        removed = 0
        errors: list[str] = []
        for child in path.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except Exception as exc:
                errors.append(f"{child}: {exc}")
        return {"path": str(path), "removed": removed, "errors": errors}

    def truncate_file(self, path) -> dict[str, object]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
            return {"path": str(path), "ok": True}
        except Exception as exc:
            return {"path": str(path), "ok": False, "error": str(exc)}

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.reminder_thread.is_alive():
            self.reminder_thread.join(timeout=5)

    def reminder_loop(self) -> None:
        while not self.stop_event.wait(max(5, self.config.reminder_poll_seconds)):
            try:
                self.dispatch_due_reminders()
            except Exception:
                self.logger.exception("Reminder scheduler failed")

    def dispatch_due_reminders(self) -> None:
        session = self.get_reminder_session()
        if session.status == "running":
            return
        due = self.reminder_store.due_reminders(datetime.now(timezone.utc), limit=1)
        if not due:
            return
        reminder = due[0]
        self.reminder_store.mark_running(reminder.id)
        worker = threading.Thread(
            target=self.run_reminder_turn,
            args=(reminder.id,),
            name=f"ThursdayReminder-{reminder.id[:8]}",
            daemon=True,
        )
        worker.start()

    def run_reminder_turn(self, reminder_id: str) -> None:
        try:
            reminder = self.reminder_store.get_reminder(reminder_id)
            session = self.get_reminder_session()
            with session.lock:
                session.add_event("reminder", "Scheduled reminder is due", reminder=reminder.public())
                self.session_store.save(session)
            self.orchestrator.run_turn(session, self.reminder_prompt(reminder), self.current_settings_patch())
            ok = session.status != "error"
            error = ""
            if not ok and session.recent_errors:
                error = session.recent_errors[-1]
            finished = self.reminder_store.mark_finished(reminder_id, ok=ok, error=error)
            with session.lock:
                session.add_event("reminder", "Scheduled reminder run finished", reminder=finished.public(), ok=ok)
                self.session_store.save(session)
        except Exception as exc:
            self.logger.exception("Reminder run failed")
            try:
                self.reminder_store.mark_finished(reminder_id, ok=False, error=str(exc))
            except Exception:
                self.logger.exception("Failed to mark reminder run as failed")

    def get_reminder_session(self) -> Session:
        with self.lock:
            for session in self.sessions.values():
                if session.title == REMINDER_SESSION_TITLE:
                    self.apply_current_preferences_to_session(session)
                    return session
            settings = AgentSettings(**asdict(self.preferences.settings))
            settings.enabled_tools = list(self.preferences.enabled_tools)
            session = Session(
                id=uuid.uuid4().hex,
                title=REMINDER_SESSION_TITLE,
                settings=settings,
                event_history_limit=self.config.event_history_limit,
            )
            self.sessions[session.id] = session
            self.session_store.save(session)
            return session

    def apply_current_preferences_to_session(self, session: Session) -> None:
        session.settings = AgentSettings(**asdict(self.preferences.settings))
        session.settings.enabled_tools = list(self.preferences.enabled_tools)
        self.session_store.save(session)

    def current_settings_patch(self) -> dict[str, object]:
        settings = asdict(self.preferences.settings)
        settings["enabled_tools"] = list(self.preferences.enabled_tools)
        settings = self.with_model_context_settings(settings)
        return settings

    def with_model_context_settings(self, settings: dict[str, object]) -> dict[str, object]:
        patched = dict(settings or {})
        endpoint = str(patched.get("endpoint") or self.config.lmstudio_endpoint)
        model = str(patched.get("model") or self.config.lmstudio_model)
        fallback = self.safe_int(patched.get("context_window"), self.config.default_context_window)
        context = self.orchestrator.llm.model_context_window(endpoint, model, fallback)
        patched["context_window"] = int(context["context_window"])
        patched["max_tokens"] = min(
            self.safe_int(patched.get("max_tokens"), self.config.default_max_tokens),
            int(context["context_window"]),
        )
        return patched

    def apply_model_context_to_agent_settings(self, settings: AgentSettings) -> None:
        context_settings = self.with_model_context_settings(asdict(settings))
        settings.context_window = int(context_settings["context_window"])
        settings.max_tokens = int(context_settings["max_tokens"])

    def safe_int(self, value: object, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return int(fallback)

    def reminder_prompt(self, reminder: Reminder) -> str:
        return (
            "Scheduled reminder due now.\n\n"
            f"Reminder title: {reminder.title}\n"
            f"Original user task: {reminder.prompt}\n"
            f"Schedule: {reminder.recurrence} at {reminder.time_of_day} {reminder.timezone_name}\n"
            f"Interval minutes: {reminder.interval_minutes}\n"
            f"Day of month: {reminder.day_of_month}\n"
            f"Weekdays: {', '.join(reminder.weekdays) if reminder.weekdays else 'none'}\n"
            f"Reminder id: {reminder.id}\n\n"
            "Run this as a normal Thursday agent turn. Use tools when the task needs live or verified information. "
            "Do not merely acknowledge the reminder. Complete the reminder task and give the user the useful result."
        )
