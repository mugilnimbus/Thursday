from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from .config import AppConfig
from .models import AgentSettings
from .sqlite_logging import insert_raw_lmstudio_log


class LMStudioChatClient:
    _raw_log_lock = threading.Lock()

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def chat(self, messages: list[dict[str, Any]], settings: AgentSettings, tools: list[dict[str, Any]]) -> dict[str, Any]:
        endpoint = settings.endpoint.rstrip("/")
        request_id = uuid.uuid4().hex
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": self._sanitize_messages(messages),
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens,
            "stream": False,
            "stop": self.config.llm_stop_sequences,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return self._create_chat_completion(endpoint, payload, request_id, attempt=1, compatibility_mode=False)

    def status(self, endpoint: str | None = None) -> dict[str, Any]:
        resolved_endpoint = (endpoint or self.config.lmstudio_endpoint).rstrip("/")
        try:
            with httpx.Client(timeout=self.config.status_timeout_seconds) as client:
                response = client.get(f"{resolved_endpoint}/v1/models")
                response.raise_for_status()
                data = response.json()
            return {"ok": True, "endpoint": resolved_endpoint, "models": data.get("data", data)}
        except Exception as exc:
            return {"ok": False, "endpoint": resolved_endpoint, "error": str(exc)}

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            tool_calls = message.get("tool_calls")

            if role == "assistant" and not content and not tool_calls:
                continue

            clean: dict[str, Any] = {"role": role}
            if content is not None:
                clean["content"] = content

            if role == "assistant" and tool_calls:
                clean["tool_calls"] = tool_calls
            if role == "tool":
                if message.get("tool_call_id"):
                    clean["tool_call_id"] = message["tool_call_id"]
                if message.get("name"):
                    clean["name"] = message["name"]

            if role in {"system", "user", "assistant", "tool"}:
                sanitized.append(clean)

        return sanitized

    def _create_chat_completion(
        self,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str,
        attempt: int,
        compatibility_mode: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        url = f"{endpoint}/v1/chat/completions"
        client = OpenAI(
            base_url=f"{endpoint}/v1",
            api_key="lm-studio",
            timeout=self.config.llm_timeout_seconds,
            max_retries=0,
        )
        request_kwargs = self.openai_request_kwargs(payload)
        try:
            raw_response = client.chat.completions.with_raw_response.create(**request_kwargs)
            parsed_response = raw_response.parse()
            response_json = self.model_to_json(parsed_response)
            self._write_raw_log({
                "timestamp": timestamp,
                "request_id": request_id,
                "kind": "lmstudio.openai.chat.completions",
                "attempt": attempt,
                "compatibility_mode": compatibility_mode,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload},
                "response": {
                    "status_code": self.raw_response_status(raw_response),
                    "headers": self.raw_response_headers(raw_response),
                    "text": json.dumps(response_json, ensure_ascii=False),
                    "json": response_json,
                    "json_error": "",
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            return response_json
        except APIStatusError as exc:
            response_text = ""
            response_json: Any = None
            response_json_error = ""
            response = getattr(exc, "response", None)
            if response is not None:
                response_text = getattr(response, "text", "") or ""
                try:
                    response_json = response.json()
                except Exception as json_exc:
                    response_json_error = str(json_exc)
            self._write_raw_log({
                "timestamp": timestamp,
                "request_id": request_id,
                "kind": "lmstudio.openai.chat.completions",
                "attempt": attempt,
                "compatibility_mode": compatibility_mode,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload},
                "response": {
                    "status_code": getattr(exc, "status_code", None),
                    "headers": dict(getattr(response, "headers", {}) or {}) if response is not None else {},
                    "text": response_text,
                    "json": response_json,
                    "json_error": response_json_error,
                },
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            raise
        except Exception as exc:
            self._write_raw_log({
                "timestamp": timestamp,
                "request_id": request_id,
                "kind": "lmstudio.openai.chat.completions",
                "attempt": attempt,
                "compatibility_mode": compatibility_mode,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload},
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            raise

    def openai_request_kwargs(self, payload: dict[str, Any]) -> dict[str, Any]:
        openai_fields = {
            "model",
            "messages",
            "temperature",
            "top_p",
            "max_tokens",
            "stream",
            "stop",
            "tools",
            "tool_choice",
        }
        request_kwargs = {key: value for key, value in payload.items() if key in openai_fields}
        extra_body = {key: value for key, value in payload.items() if key not in openai_fields}
        if extra_body:
            request_kwargs["extra_body"] = extra_body
        return request_kwargs

    def model_to_json(self, value: Any) -> dict[str, Any]:
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(mode="json")
            return dumped if isinstance(dumped, dict) else {"value": dumped}
        if isinstance(value, dict):
            return value
        return json.loads(json.dumps(value, default=str))

    def raw_response_status(self, raw_response: Any) -> int | None:
        status = getattr(raw_response, "status_code", None)
        if status is not None:
            return int(status)
        http_response = getattr(raw_response, "http_response", None)
        status = getattr(http_response, "status_code", None)
        return int(status) if status is not None else None

    def raw_response_headers(self, raw_response: Any) -> dict[str, str]:
        headers = getattr(raw_response, "headers", None)
        if headers is None:
            http_response = getattr(raw_response, "http_response", None)
            headers = getattr(http_response, "headers", None)
        return {str(key): str(value) for key, value in dict(headers or {}).items()}

    def _write_raw_log(self, record: dict[str, Any]) -> None:
        if not self.config.lmstudio_raw_log_enabled:
            return
        with self._raw_log_lock:
            insert_raw_lmstudio_log(self.config.log_db_file, record)
