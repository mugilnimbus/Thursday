from __future__ import annotations

import json
import logging
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .sqlite_logging import fetch_raw_lmstudio_logs, fetch_recent_logs
from .state import AppState


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def make_handler(config: AppConfig, state: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "ThursdayDashboard/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            logging.getLogger("agent.http").info("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/config":
                json_response(self, 200, config.public())
                return
            if path == "/api/status":
                params = parse_qs(parsed.query)
                endpoint = params.get("endpoint", [config.lmstudio_endpoint])[0]
                json_response(self, 200, state.orchestrator.llm.status(endpoint))
                return
            if path == "/api/tools":
                json_response(
                    self,
                    200,
                    {
                        "tools": state.orchestrator.tools.definitions(state.preferences.enabled_tools),
                        "all_tools": state.orchestrator.tools.definitions(),
                        "enabled_tools": state.preferences.enabled_tools,
                        "workspace": state.orchestrator.tools.workspace_label(),
                    },
                )
                return
            if path == "/api/workspace":
                json_response(self, 200, state.workspace_status())
                return
            if path == "/api/preferences":
                json_response(self, 200, state.preferences_public())
                return
            if path == "/api/reminders":
                params = parse_qs(parsed.query)
                include_disabled = params.get("include_disabled", ["true"])[0].strip().lower() not in {"0", "false", "no", "off"}
                json_response(self, 200, state.reminders_public(include_disabled=include_disabled))
                return
            if path == "/api/logs":
                params = parse_qs(parsed.query)
                try:
                    limit = int(params.get("limit", ["120"])[0])
                except ValueError:
                    limit = 120
                source = params.get("source", [""])[0]
                json_response(self, 200, {"file": str(config.log_db_file), "logs": fetch_recent_logs(config.log_db_file, limit, source=source)})
                return
            if path == "/api/lmstudio/raw-log":
                params = parse_qs(parsed.query)
                try:
                    limit = int(params.get("limit", ["40"])[0])
                except ValueError:
                    limit = 40
                json_response(self, 200, fetch_raw_lmstudio_logs(config.log_db_file, limit))
                return
            if path == "/api/sessions":
                with state.lock:
                    sessions = [session.to_public() for session in state.sessions.values()]
                json_response(self, 200, {"sessions": sessions})
                return
            if path.startswith("/api/sessions/"):
                trace_requested = path.endswith("/trace")
                session_id = path.split("/")[-2] if trace_requested else path.rsplit("/", 1)[-1]
                with state.lock:
                    session = state.sessions.get(session_id)
                if not session:
                    json_response(self, 404, {"error": "Session not found"})
                    return
                if trace_requested:
                    json_response(self, 200, session.to_trace())
                    return
                json_response(self, 200, session.to_public())
                return
            self.serve_static(path)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/sessions":
                session = state.create_session()
                json_response(self, 201, session.to_public())
                return
            if parsed.path == "/api/chat":
                try:
                    payload = read_json(self)
                    message = str(payload.get("message", "")).strip()
                    if not message:
                        json_response(self, 400, {"error": "message is required"})
                        return
                    session = state.get_session(payload.get("session_id"))
                    settings = payload.get("settings") or {}
                    thread = threading.Thread(
                        target=state.orchestrator.run_turn,
                        args=(session, message, settings),
                        daemon=True,
                    )
                    thread.start()
                    json_response(self, 202, {"session_id": session.id, "status": "running"})
                except Exception as exc:
                    json_response(self, 500, {"error": str(exc)})
                return
            if parsed.path == "/api/preferences":
                payload = read_json(self)
                restore = bool(payload.get("restore_defaults"))
                if restore:
                    json_response(self, 200, state.restore_preferences())
                else:
                    json_response(self, 200, state.update_preferences(payload))
                return
            if parsed.path == "/api/workspace/reset":
                result = state.reset_workspace()
                json_response(self, 200 if result.get("ok") else 409, result)
                return
            if parsed.path == "/api/reminders":
                payload = read_json(self)
                try:
                    reminder = state.reminder_store.create_reminder(
                        title=str(payload.get("title") or ""),
                        prompt=str(payload.get("prompt") or ""),
                        recurrence=str(payload.get("recurrence") or "daily"),
                        time_of_day=str(payload.get("time") or payload.get("time_of_day") or "09:00"),
                        timezone_name=str(payload.get("timezone") or payload.get("timezone_name") or config.reminder_timezone),
                        date_value=str(payload.get("date") or payload.get("date_value") or ""),
                        weekdays=[str(item) for item in payload.get("weekdays", [])] if isinstance(payload.get("weekdays", []), list) else [],
                        enabled=bool(payload.get("enabled", True)),
                    )
                    json_response(self, 201, {"ok": True, "reminder": reminder.public()})
                except Exception as exc:
                    json_response(self, 400, {"ok": False, "error": str(exc)})
                return
            json_response(self, 404, {"error": "Not found"})

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/sessions/"):
                session_id = parsed.path.rsplit("/", 1)[-1]
                if not session_id:
                    json_response(self, 400, {"error": "session id is required"})
                    return
                if state.delete_session(session_id):
                    json_response(self, 200, {"ok": True, "deleted": session_id})
                    return
                json_response(self, 404, {"error": "Session not found"})
                return
            if parsed.path.startswith("/api/reminders/"):
                reminder_id = parsed.path.rsplit("/", 1)[-1]
                if state.reminder_store.delete_reminder(reminder_id):
                    json_response(self, 200, {"ok": True, "deleted": reminder_id})
                    return
                json_response(self, 404, {"ok": False, "error": "Reminder not found"})
                return
            json_response(self, 404, {"error": "Not found"})

        def serve_static(self, path: str) -> None:
            if path == "/":
                target = config.static_dir / "index.html"
            else:
                target = (config.static_dir / path.lstrip("/")).resolve()
                if config.static_dir not in target.parents and target != config.static_dir:
                    self.send_error(403)
                    return
            if not target.exists() or not target.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def create_server(config: AppConfig, state: AppState) -> ThreadingHTTPServer:
    handler = make_handler(config, state)
    return ThreadingHTTPServer((config.server_host, config.server_port), handler)
