from __future__ import annotations

import json
import base64
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from ..config import AppConfig
from ..models import AgentSettings
from ..logging_store import insert_raw_lmstudio_log
from ..utils import estimate_tokens


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
        messages_for_transport = self._messages_for_transport(self._sanitize_messages(messages))
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
            "input": self.responses_input_items(messages),
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_output_tokens": self.max_tokens_for_context(settings, prompt_estimate),
            "stream": False,
            "store": True,
            "truncation": "auto",
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        converted_tools = self.responses_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            payload["tool_choice"] = "auto"
        return payload

    def responses_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools or []:
            function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
            name = function.get("name")
            if not name:
                continue
            converted.append({
                "type": "function",
                "name": name,
                "description": str(function.get("description") or ""),
                "parameters": function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object", "properties": {}},
            })
        return converted

    def responses_input_items(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "tool":
                items.append({
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or message.get("id") or ""),
                    "output": self.message_content_to_text(content),
                })
                continue

            if role in {"system", "user", "assistant"}:
                text = self.message_content_to_text(content)
                if text:
                    items.append({"role": role, "content": text})
                if role == "assistant":
                    for call in message.get("tool_calls") or []:
                        function = call.get("function", {}) if isinstance(call.get("function"), dict) else {}
                        name = function.get("name") or call.get("name")
                        if not name:
                            continue
                        items.append({
                            "type": "function_call",
                            "call_id": str(call.get("id") or call.get("call_id") or f"call_{uuid.uuid4().hex[:10]}"),
                            "name": str(name),
                            "arguments": self.message_content_to_text(function.get("arguments") or call.get("arguments") or "{}"),
                        })
        return items

    def _create_response(
        self,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str,
        attempt: int,
        fallback_from_previous: bool,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat()
        url = f"{endpoint}/v1/responses"
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
                "compatibility_mode": False,
                "fallback_from_previous_response_id": fallback_from_previous,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": self.payload_for_log(payload)},
                "response": {
                    "status_code": response.status_code,
                    "headers": dict(response.headers),
                    "text": json.dumps(response_json, ensure_ascii=False),
                    "json": response_json,
                    "json_error": "",
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            })
            return self.response_to_chat_completion(response_json, payload, fallback_from_previous)
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
            self._write_raw_log({
                "timestamp": timestamp,
                "request_id": request_id,
                "kind": "lmstudio.openai.responses",
                "attempt": attempt,
                "compatibility_mode": False,
                "fallback_from_previous_response_id": fallback_from_previous,
                "method": "POST",
                "url": url,
                "request": {"headers": {"Content-Type": "application/json"}, "json": self.payload_for_log(payload)},
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
            raise

    def response_to_chat_completion(
        self,
        response_json: dict[str, Any],
        request_payload: dict[str, Any],
        fallback_from_previous: bool,
    ) -> dict[str, Any]:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in response_json.get("output") or []:
            item_type = item.get("type")
            if item_type == "reasoning":
                for part in item.get("content") or []:
                    text = part.get("text") if isinstance(part, dict) else ""
                    if text:
                        thinking_parts.append(str(text))
                continue
            if item_type == "message":
                for part in item.get("content") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"output_text", "text"} and part.get("text") is not None:
                        content_parts.append(str(part.get("text") or ""))
                continue
            if item_type == "function_call":
                call_id = str(item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:10]}")
                tool_calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": self.message_content_to_text(item.get("arguments") or "{}"),
                    },
                })

        finish_reason = "tool_calls" if tool_calls else self.responses_finish_reason(response_json)
        message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if thinking_parts:
            message["thinking"] = "\n".join(thinking_parts)
        if tool_calls:
            message["tool_calls"] = tool_calls

        response_id = str(response_json.get("id") or "")
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": response_json.get("created_at"),
            "model": response_json.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": response_json.get("usage") or {},
            "_thursday": {
                "api": "responses",
                "response_id": response_id,
                "previous_response_id": request_payload.get("previous_response_id") or "",
                "used_previous_response_id": bool(request_payload.get("previous_response_id")),
                "fallback_from_previous_response_id": fallback_from_previous,
                "cached_tokens": ((response_json.get("usage") or {}).get("input_tokens_details") or {}).get("cached_tokens", 0),
            },
        }

    def responses_finish_reason(self, response_json: dict[str, Any]) -> str:
        if response_json.get("status") == "incomplete":
            reason = (response_json.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                return "length"
            return str(reason or "incomplete")
        if response_json.get("error"):
            return "error"
        return "stop"

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

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        sanitized: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            tool_calls = message.get("tool_calls")
            images = message.get("images")

            if role == "assistant" and not content and not tool_calls:
                continue

            clean: dict[str, Any] = {"role": role}
            if content is not None:
                clean["content"] = content
            if isinstance(images, list) and images:
                clean["images"] = self._sanitize_images(images)

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
                "request": {"headers": {"Content-Type": "application/json"}, "json": self.payload_for_log(payload)},
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
                "request": {"headers": {"Content-Type": "application/json"}, "json": self.payload_for_log(payload)},
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
                "request": {"headers": {"Content-Type": "application/json"}, "json": self.payload_for_log(payload)},
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

    def _messages_for_transport(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        pending_tool_image_messages: list[dict[str, Any]] = []
        fresh_tool_image_indexes = self._fresh_tool_image_indexes(messages)
        for index, message in enumerate(messages):
            clean = dict(message)
            images = clean.pop("images", []) if index in fresh_tool_image_indexes else []
            clean.pop("llm_images_consumed", None)
            if clean.get("role") == "tool":
                clean["content"] = self.message_content_to_text(message.get("content"))
                prepared.append(clean)
                if images:
                    pending_tool_image_messages.append(self._tool_images_as_user_message(clean, images))
                continue
            if pending_tool_image_messages:
                prepared.extend(pending_tool_image_messages)
                pending_tool_image_messages.clear()
            clean["content"] = self._content_for_transport(message.get("content"), images)
            prepared.append(clean)
        if pending_tool_image_messages:
            prepared.extend(pending_tool_image_messages)
        return prepared

    def _fresh_tool_image_indexes(self, messages: list[dict[str, Any]]) -> set[int]:
        fresh: set[int] = set()
        assistant_seen_after = False
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            role = message.get("role")
            if role == "assistant":
                assistant_seen_after = True
            if (
                role == "tool"
                and message.get("images")
                and not message.get("llm_images_consumed")
                and not assistant_seen_after
            ):
                fresh.add(index)
        return fresh

    def _tool_images_as_user_message(self, tool_message: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any]:
        tool_name = str(tool_message.get("name") or "tool")
        return {
            "role": "user",
            "content": self._content_for_transport(
                f"Visual output from `{tool_name}`. Inspect the attached image(s) together with the preceding tool result.",
                images,
            ),
        }

    def _sanitize_images(self, images: list[Any]) -> list[dict[str, str]]:
        sanitized: list[dict[str, str]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            path = str(image.get("path") or "")
            if not path:
                continue
            sanitized.append(
                {
                    "path": path,
                    "mime_type": str(image.get("mime_type") or "image/png"),
                    "name": str(image.get("name") or image.get("source") or Path(path).name or "image"),
                }
            )
        return sanitized

    def _content_for_transport(self, content: Any, images: list[dict[str, Any]]) -> Any:
        if not images:
            return content

        text = self.message_content_to_text(content)
        prepared: list[Any] = [{"type": "text", "text": text or "Please analyze the attached image(s)."}]
        for image in images:
            path = Path(str(image.get("path") or ""))
            mime_type = str(image.get("mime_type") or "image/png")
            prepared.append({"type": "image_url", "image_url": {"url": self.image_data_url(path, mime_type)}})
        return prepared

    def message_content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    def image_data_url(self, path: Path, mime_type: str) -> str:
        resolved = path.resolve()
        allowed_roots = [self.config.image_upload_dir.resolve(), self.config.visual_check_dir.resolve()]
        if not any(root == resolved or root in resolved.parents for root in allowed_roots):
            raise ValueError(f"Image path is outside allowed image directories: {resolved}")
        data = base64.b64encode(resolved.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{data}"

    def payload_for_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._redact_image_data(payload)

    def _redact_image_data(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._redact_image_data(item) for item in value]
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                if key == "url" and isinstance(item, str) and item.startswith("data:image/"):
                    redacted[key] = f"[image data URL redacted, chars={len(item)}]"
                else:
                    redacted[key] = self._redact_image_data(item)
            return redacted
        return value

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
