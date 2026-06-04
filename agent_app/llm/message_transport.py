from __future__ import annotations

import base64
import json
import uuid
from pathlib import Path
from typing import Any

from ..runtime.config import AppConfig


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            clean["images"] = sanitize_images(images)

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


def messages_for_transport(messages: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    pending_tool_image_messages: list[dict[str, Any]] = []
    fresh_tool_image_indexes = fresh_tool_image_indexes_for(messages)
    for index, message in enumerate(messages):
        clean = dict(message)
        images = []
        if index in fresh_tool_image_indexes or clean.get("role") == "user":
            images = clean.pop("images", [])
        clean.pop("llm_images_consumed", None)
        if clean.get("role") == "tool":
            clean["content"] = message_content_to_text(message.get("content"))
            prepared.append(clean)
            if images:
                pending_tool_image_messages.append(tool_images_as_user_message(clean, images, config))
            continue
        if pending_tool_image_messages:
            prepared.extend(pending_tool_image_messages)
            pending_tool_image_messages.clear()
        clean["content"] = content_for_transport(message.get("content"), images, config)
        prepared.append(clean)
    if pending_tool_image_messages:
        prepared.extend(pending_tool_image_messages)
    return prepared


def fresh_tool_image_indexes_for(messages: list[dict[str, Any]]) -> set[int]:
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


def tool_images_as_user_message(
    tool_message: dict[str, Any],
    images: list[dict[str, Any]],
    config: AppConfig,
) -> dict[str, Any]:
    tool_name = str(tool_message.get("name") or "tool")
    return {
        "role": "user",
        "content": content_for_transport(
            (
                f"Visual output from `{tool_name}` is attached as image input. "
                "Inspect the attached image(s) directly together with the preceding tool result. "
                "Do not answer that you only have file paths or that you cannot see the screenshot. "
                "If the image is unclear, describe the visible state and name what is uncertain."
            ),
            images,
            config,
        ),
    }


def sanitize_images(images: list[Any]) -> list[dict[str, str]]:
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


def content_for_transport(content: Any, images: list[dict[str, Any]], config: AppConfig) -> Any:
    if not images:
        return content

    text = message_content_to_text(content)
    prepared: list[Any] = [{"type": "text", "text": text or "Please analyze the attached image(s)."}]
    for image in images:
        path = Path(str(image.get("path") or ""))
        mime_type = str(image.get("mime_type") or "image/png")
        prepared.append({"type": "image_url", "image_url": {"url": image_data_url(path, mime_type, config)}})
    return prepared


def message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def image_data_url(path: Path, mime_type: str, config: AppConfig) -> str:
    resolved = path.resolve()
    allowed_roots = [config.image_upload_dir.resolve(), config.visual_check_dir.resolve()]
    if not any(root == resolved or root in resolved.parents for root in allowed_roots):
        raise ValueError(f"Image path is outside allowed image directories: {resolved}")
    data = base64.b64encode(resolved.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


def responses_input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(message.get("tool_call_id") or message.get("id") or ""),
                "output": message_content_to_text(content),
            })
            continue

        if role in {"system", "user", "assistant"}:
            response_content = responses_message_content(content)
            if response_content:
                items.append({"role": role, "content": response_content})
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
                        "arguments": message_content_to_text(function.get("arguments") or call.get("arguments") or "{}"),
                    })
    return items


def responses_message_content(content: Any) -> Any:
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, str):
                if item:
                    parts.append({"type": "input_text", "text": item})
                continue
            if not isinstance(item, dict):
                text = message_content_to_text(item)
                if text:
                    parts.append({"type": "input_text", "text": text})
                continue

            item_type = item.get("type")
            if item_type in {"text", "input_text"}:
                text = str(item.get("text") or "")
                if text:
                    parts.append({"type": "input_text", "text": text})
                continue
            if item_type in {"image_url", "input_image"}:
                image_url = item.get("image_url")
                if isinstance(image_url, dict):
                    image_url = image_url.get("url")
                if isinstance(image_url, str) and image_url:
                    parts.append({"type": "input_image", "image_url": image_url})
                continue

            text = message_content_to_text(item)
            if text:
                parts.append({"type": "input_text", "text": text})
        return parts

    return message_content_to_text(content)
