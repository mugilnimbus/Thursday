from __future__ import annotations

import threading
import uuid
from dataclasses import asdict

from .config import AppConfig
from .models import AgentSettings, Session
from .orchestrator import AgentOrchestrator
from .preferences import DashboardPreferences, PreferenceStore
from .session_store import SessionStore
from .workspace import docker_workspace_status, reset_docker_workspace


class AppState:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.session_store = SessionStore(config.log_db_file, config.event_history_limit)
        self.sessions: dict[str, Session] = self.session_store.load_all()
        self.lock = threading.RLock()
        self.orchestrator = AgentOrchestrator(config)
        self.orchestrator.session_update_callback = self.session_store.save
        self.preference_store = PreferenceStore(
            config.preferences_file,
            config,
            self.orchestrator.tools.names(),
        )
        self.preferences = self.preference_store.load()
        for session in self.sessions.values():
            self.session_store.save(session)

    def preferences_public(self) -> dict[str, object]:
        with self.lock:
            return self.preferences.to_public(self.config, self.orchestrator.tools.names())

    def update_preferences(self, payload: dict[str, object]) -> dict[str, object]:
        with self.lock:
            settings = AgentSettings.from_config(self.config)
            for key, value in (payload.get("settings") or {}).items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            enabled_tools = [
                str(name)
                for name in payload.get("enabled_tools", self.preferences.enabled_tools)
                if str(name) in self.orchestrator.tools.names()
            ]
            settings.enabled_tools = enabled_tools
            self.preferences = DashboardPreferences(settings=settings, enabled_tools=enabled_tools)
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

    def workspace_status(self) -> dict[str, object]:
        return docker_workspace_status(self.config)

    def reset_workspace(self) -> dict[str, object]:
        with self.lock:
            running_sessions = [
                session.title or session.id
                for session in self.sessions.values()
                if session.status == "running"
            ]
            if running_sessions:
                return {
                    "ok": False,
                    "error": "Cannot reset the Docker workspace while an agent turn is running.",
                    "running_sessions": running_sessions,
                    "status": docker_workspace_status(self.config),
                }
        return reset_docker_workspace(self.config)
