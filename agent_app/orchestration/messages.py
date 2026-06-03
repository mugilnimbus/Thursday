from __future__ import annotations

import json
from typing import Any


class ConversationMessageBuilder:
    """Normalizes model, user, and image messages for the orchestration loop."""

    def normalize_input_images(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for image in images[:6]:
            mime_type = str(image.get("mime_type") or image.get("type") or "image/png")
            name = str(image.get("name") or "image")
            path = str(image.get("path") or "")
            if not path:
                continue
            normalized.append(
                {
                    "name": name,
                    "mime_type": mime_type,
                    "size": int(image.get("size") or 0),
                    "path": path,
                    "url": str(image.get("url") or ""),
                }
            )
        return normalized

    def visible_images(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": image["name"],
                "mime_type": image["mime_type"],
                "size": image.get("size", 0),
                "path": image["path"],
                "url": image.get("url", ""),
            }
            for image in images
        ]

    def user_content(self, text: str, images: list[dict[str, Any]]) -> str:
        if text:
            return text
        if images:
            return "Please analyze the attached image(s)."
        return ""

    def first_choice(self, raw: dict[str, Any]) -> dict[str, Any]:
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return choices[0]
        return {}

    def normalize_assistant_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content_text = self.message_content_to_text(message.get("content"))
        content_without_thinking, inline_thinking = self.extract_think_blocks(content_text)

        normalized: dict[str, Any] = {"role": "assistant", "content": content_without_thinking.strip()}
        if inline_thinking.strip():
            normalized["thinking"] = inline_thinking.strip()
        if message.get("tool_calls"):
            normalized["tool_calls"] = message["tool_calls"]
        return normalized

    def strip_final_answer_marker(self, content: str) -> tuple[str, bool]:
        marker = "</Final_answer>"
        if marker not in content:
            return content, False
        return content.replace(marker, "").strip(), True

    def message_content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                    elif text is not None:
                        parts.append(json.dumps(text, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(value, ensure_ascii=False)

    def extract_think_blocks(self, content: str) -> tuple[str, str]:
        if "<think>" not in content:
            return content, ""

        visible_parts: list[str] = []
        thinking_parts: list[str] = []
        cursor = 0
        while True:
            start = content.find("<think>", cursor)
            if start == -1:
                visible_parts.append(content[cursor:])
                break
            visible_parts.append(content[cursor:start])
            end = content.find("</think>", start + len("<think>"))
            if end == -1:
                thinking_parts.append(content[start + len("<think>") :])
                break
            thinking_parts.append(content[start + len("<think>") : end])
            cursor = end + len("</think>")

        visible = "".join(visible_parts).strip()
        thinking = "\n\n".join(part.strip() for part in thinking_parts if part.strip())
        return visible, thinking
