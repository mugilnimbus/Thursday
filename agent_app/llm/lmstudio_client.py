from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from ..runtime.config import AppConfig
from ..runtime.models import AgentSettings
from ..logging import insert_raw_lmstudio_log
from ..utils import estimate_tokens
from .message_transport import (
    message_content_to_text,
    messages_for_transport as build_messages_for_transport,
    responses_input_items,
    sanitize_messages,
)
from .response_parsing import (
    model_to_json,
    payload_for_log,
    raw_response_headers,
    raw_response_status,
    response_to_chat_completion,
    responses_finish_reason,
    responses_tools,
)


class LMStudioChatClient:
    _raw_log_lock = threading.Lock()

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._status_cache_lock = threading.Lock()

    def chat(
        self,
        messages: list[dict[str, Any]],
        settings: AgentSettings,
        tools: list[dict[str, Any]],
        previous_response_id: str = "",
        response_anchor_message_count: int = 0,
    ) -> dict[str, Any]:
        endpoint = settings.endpoint.rstrip("/")
        request_id = uuid.uuid4().hex
        messages_for_transport = build_messages_for_transport(sanitize_messages(messages), self.config)
        prompt_estimate = estimate_tokens(json.dumps(messages_for_transport, ensure_ascii=False))
        if self.config.use_responses_api:
            try:
                return self._responses_chat(
                    endpoint=endpoint,
                    messages_for_transport=messages_for_transport,
                    settings=settings,
                    tools=tools,
                    request_id=request_id,
                    prompt_estimate=prompt_estimate,
                    previous_response_id=previous_response_id,
                    response_anchor_message_count=response_anchor_message_count,
                )
            except Exception as exc:
                if self._is_model_unavailable_exception(exc):
                    raise
                self._write_raw_log({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "request_id": request_id,
                    "kind": "thursday.responses.compatibility_fallback",
                    "attempt": 1,
                    "compatibility_mode": True,
                    "method": "POST",
                    "url": f"{endpoint}/v1/chat/completions",
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "elapsed_ms": 0,
                })

        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages_for_transport,
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": self.max_tokens_for_context(settings, prompt_estimate),
            "stream": False,
            "stop": self.config.llm_stop_sequences,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return self._create_chat_completion(endpoint, payload, request_id, attempt=1, compatibility_mode=False)

    def _responses_chat(
        self,
        endpoint: str,
        messages_for_transport: list[dict[str, Any]],
        settings: AgentSettings,
        tools: list[dict[str, Any]],
        request_id: str,
        prompt_estimate: int,
        previous_response_id: str,
        response_anchor_message_count: int,
    ) -> dict[str, Any]:
        use_previous = bool(previous_response_id and response_anchor_message_count > 0)
        delta_messages = messages_for_transport[response_anchor_message_count:] if use_previous else messages_for_transport
        payload = self.responses_payload(
            messages=delta_messages,
            settings=settings,
            tools=tools,
            prompt_estimate=prompt_estimate,
            previous_response_id=previous_response_id if use_previous else "",
        )
        try:
            return self._create_response(endpoint, payload, request_id, attempt=1, fallback_from_previous=False)
        except Exception as exc:
            if not use_previous:
                raise
            fallback_payload = self.responses_payload(
                messages=messages_for_transport,
                settings=settings,
                tools=tools,
                prompt_estimate=prompt_estimate,
                previous_response_id="",
            )
            result = self._create_response(endpoint, fallback_payload, request_id, attempt=2, fallback_from_previous=True)
            result.setdefault("_thursday", {})
            result["_thursday"]["previous_response_failed"] = {
                "id": previous_response_id,
                "error": str(exc),
                "type": type(exc).__name__,
            }
            return result

    def responses_payload(
        self,
        messages: list[dict[str, Any]],
        settings: AgentSettings,
        tools: list[dict[str, Any]],
        prompt_estimate: int,
        previous_response_id: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": settings.model,
            "input": responses_input_items(messages),
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_output_tokens": self.max_tokens_for_context(settings, prompt_estimate),
            "stream": False,
            "store": True,
            "truncation": "auto",
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        converted_tools = responses_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"
        return payload



    def _create_response(
        self,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str,
        attempt: int,
        fallback_from_previous: bool,
    ) -> dict[str, Any]:
        url = f"{endpoint}/v1/responses"
        max_transport_attempts = 3
        for transport_attempt in range(1, max_transport_attempts + 1):
            started = time.perf_counter()
            timestamp = datetime.now(timezone.utc).isoformat()
            try:
                with httpx.Client(timeout=self.config.llm_timeout_seconds) as client:
                    response = client.post(url, json=payload)
                    response.raise_for_status()
                    response_json = response.json()
                self._write_raw_log({
                    "timestamp": timestamp,
                    "request_id": request_id,
                    "kind": "lmstudio.openai.responses",
                    "attempt": attempt,
                    "transport_attempt": transport_attempt,
                    "compatibility_mode": False,
                    "fallback_from_previous_response_id": fallback_from_previous,
                    "method": "POST",
                    "url": url,
                    "request": {"headers": {"Content-Type": "application/json"}, "json": payload_for_log(payload)},
                    "response": {
                        "status_code": response.status_code,
                        "headers": dict(response.headers),
                        "text": json.dumps(response_json, ensure_ascii=False),
                        "json": response_json,
                        "json_error": "",
                    },
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                })
                return response_to_chat_completion(response_json, payload, fallback_from_previous)
            except Exception as exc:
                response = getattr(exc, "response", None)
                response_text = getattr(response, "text", "") if response is not None else ""
                response_json: Any = None
                response_json_error = ""
                if response is not None:
                    try:
                        response_json = response.json()
                    except Exception as json_exc:
                        response_json_error = str(json_exc)
                transient = self._is_transient_lmstudio_error(exc, response_text, response_json)
                self._write_raw_log({
                    "timestamp": timestamp,
                    "request_id": request_id,
                    "kind": "lmstudio.openai.responses",
                    "attempt": attempt,
                    "transport_attempt": transport_attempt,
                    "transient_retry": transient and transport_attempt < max_transport_attempts,
                    "compatibility_mode": False,
                    "fallback_from_previous_response_id": fallback_from_previous,
                    "method": "POST",
                    "url": url,
                    "request": {"headers": {"Content-Type": "application/json"}, "json": payload_for_log(payload)},
                    "response": {
                        "status_code": getattr(response, "status_code", None),
                        "headers": dict(getattr(response, "headers", {}) or {}) if response is not None else {},
                        "text": response_text,
                        "json": response_json,
                        "json_error": response_json_error,
                    },
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                })
                if transient and transport_attempt < max_transport_attempts:
                    time.sleep(min(5, transport_attempt * 2))
                    continue
                raise

        raise RuntimeError("LM Studio response request failed after retries.")



    def max_tokens_for_context(self, settings: AgentSettings, prompt_estimate: int) -> int:
        context_window = max(1024, int(settings.context_window or self.config.default_context_window))
        reserve = min(2048, max(256, context_window // 20))
        available = context_window - int(prompt_estimate) - reserve
        return max(256, min(int(settings.max_tokens), available))

    def status(self, endpoint: str | None = None) -> dict[str, Any]:
        resolved_endpoint = (endpoint or self.config.lmstudio_endpoint).rstrip("/")
        cached = self._cached_status(resolved_endpoint)
        if cached is not None:
            return {**cached, "cached": True}

        try:
            status = self.fetch_model_status(resolved_endpoint)
        except Exception as exc:
            status = {"ok": False, "endpoint": resolved_endpoint, "error": str(exc), "models": []}
        self._store_status(resolved_endpoint, status)
        return status

    def fetch_model_status(self, endpoint: str) -> dict[str, Any]:
        with httpx.Client(timeout=self.config.status_timeout_seconds) as client:
            native_response = client.get(f"{endpoint}/api/v0/models")
            if native_response.status_code < 400:
                native_data = native_response.json()
                models = native_data.get("data", native_data)
                if isinstance(models, list):
                    return {
                        "ok": True,
                        "endpoint": endpoint,
                        "models": models,
                        "model_metadata_source": "/api/v0/models",
                    }

            response = client.get(f"{endpoint}/v1/models")
            response.raise_for_status()
            data = response.json()
            models = data.get("data", data)
            return {
                "ok": True,
                "endpoint": endpoint,
                "models": models,
                "model_metadata_source": "/v1/models",
            }

    def model_details(self, endpoint: str, model_name: str) -> dict[str, Any] | None:
        status = self.status(endpoint)
        models = status.get("models") if isinstance(status.get("models"), list) else []
        requested = str(model_name or "").strip()
        requested_alias = self.model_alias(requested)
        alias_match: dict[str, Any] | None = None
        for model in models:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "")
            if model_id == requested:
                return model
            if requested_alias and self.model_alias(model_id) == requested_alias and alias_match is None:
                alias_match = model
        return alias_match

    def model_alias(self, model_name: str) -> str:
        return str(model_name or "").strip().rstrip("/").split("/")[-1]

    def model_context_window(self, endpoint: str, model_name: str, fallback: int) -> dict[str, Any]:
        details = self.model_details(endpoint, model_name) or {}
        loaded = self.int_field(details, "loaded_context_length")
        maximum = self.int_field(details, "max_context_length")
        if loaded and loaded > 0:
            return {
                "context_window": loaded,
                "source": "loaded_context_length",
                "model": model_name,
                "model_details": details,
            }
        if maximum and maximum > 0:
            return {
                "context_window": maximum,
                "source": "max_context_length",
                "model": model_name,
                "model_details": details,
            }
        return {
            "context_window": int(fallback),
            "source": "configured_fallback",
            "model": model_name,
            "model_details": details,
        }

    def _cached_status(self, endpoint: str) -> dict[str, Any] | None:
        ttl = max(0, int(self.config.model_status_cache_seconds))
        if ttl <= 0:
            return None
        with self._status_cache_lock:
            cached = self._status_cache.get(endpoint)
            if not cached:
                return None
            cached_at, value = cached
            if time.monotonic() - cached_at > ttl:
                self._status_cache.pop(endpoint, None)
                return None
            return dict(value)

    def _store_status(self, endpoint: str, status: dict[str, Any]) -> None:
        ttl = max(0, int(self.config.model_status_cache_seconds))
        if ttl <= 0:
            return
        with self._status_cache_lock:
            self._status_cache[endpoint] = (time.monotonic(), dict(status))

    def int_field(self, payload: dict[str, Any], field: str) -> int | None:
        try:
            value = payload.get(field)
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _is_model_unavailable_exception(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        response_text = getattr(response, "text", "") if response is not None else ""
        return "No models loaded" in response_text or "No models loaded" in str(exc)

    def _is_transient_lmstudio_error(self, exc: Exception, response_text: str, response_json: Any) -> bool:
        response = getattr(exc, "response", None)
        status_code = int(getattr(response, "status_code", 0) or 0)
        combined = f"{response_text}\n{json.dumps(response_json, ensure_ascii=False, default=str) if response_json is not None else ''}\n{exc}"
        if status_code >= 500:
            return True
        transient_markers = (
            "LM Link connection closed",
            "No models loaded",
            "model is loading",
            "model not loaded",
            "server disconnected",
        )
        return any(marker.lower() in combined.lower() for marker in transient_markers)


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
            response_json = model_to_json(parsed_response)
            self._write_raw_log({
                "timestamp": timestamp,
                "request_id": request_id,
                "kind": "lmstudio.openai.chat.completions",
                "attempt": attempt,
                "compatibility_mode": compatibility_mode,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload_for_log(payload)},
                "response": {
                    "status_code": raw_response_status(raw_response),
                    "headers": raw_response_headers(raw_response),
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
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload_for_log(payload)},
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
                "request": {"headers": {"Content-Type": "application/json"}, "json": payload_for_log(payload)},
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













    def _write_raw_log(self, record: dict[str, Any]) -> None:
        if not self.config.lmstudio_raw_log_enabled:
            return
        with self._raw_log_lock:
            insert_raw_lmstudio_log(self.config.log_db_file, record)


