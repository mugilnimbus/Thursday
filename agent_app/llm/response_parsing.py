from __future__ import annotations

import json
import uuid
from typing import Any

from .message_transport import message_content_to_text


def responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def response_to_chat_completion(
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
                    "arguments": message_content_to_text(item.get("arguments") or "{}"),
                },
            })

    finish_reason = "tool_calls" if tool_calls else responses_finish_reason(response_json)
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
            "request_input_items": len(request_payload.get("input") or []),
            "request_tool_count": len(request_payload.get("tools") or []),
            "request_has_previous_response_id": bool(request_payload.get("previous_response_id")),
        },
    }


def responses_finish_reason(response_json: dict[str, Any]) -> str:
    if response_json.get("status") == "incomplete":
        reason = (response_json.get("incomplete_details") or {}).get("reason")
        if reason == "max_output_tokens":
            return "length"
        return str(reason or "incomplete")
    if response_json.get("error"):
        return "error"
    return "stop"


def payload_for_log(payload: dict[str, Any]) -> dict[str, Any]:
    return redact_image_data(payload)


def redact_image_data(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_image_data(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key == "url" and isinstance(item, str) and item.startswith("data:image/"):
                redacted[key] = f"[image data URL redacted, chars={len(item)}]"
            else:
                redacted[key] = redact_image_data(item)
        return redacted
    return value


def model_to_json(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    if isinstance(value, dict):
        return value
    return json.loads(json.dumps(value, default=str))


def raw_response_status(raw_response: Any) -> int | None:
    status = getattr(raw_response, "status_code", None)
    if status is not None:
        return int(status)
    http_response = getattr(raw_response, "http_response", None)
    status = getattr(http_response, "status_code", None)
    return int(status) if status is not None else None


def raw_response_headers(raw_response: Any) -> dict[str, str]:
    headers = getattr(raw_response, "headers", None)
    if headers is None:
        http_response = getattr(raw_response, "http_response", None)
        headers = getattr(http_response, "headers", None)
    return {str(key): str(value) for key, value in dict(headers or {}).items()}
