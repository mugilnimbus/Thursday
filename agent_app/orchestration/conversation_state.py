from __future__ import annotations

import copy
from typing import Any

from ..runtime.models import Session


def ensure_message_backup(session: Session) -> None:
    if not session.message_backup and session.messages:
        session.message_backup = [clone_message(message) for message in session.messages]


def append_conversation_message(session: Session, message: dict[str, Any], backup: bool = True) -> None:
    cloned = clone_message(message)
    session.messages.append(cloned)
    if backup:
        session.message_backup.append(clone_message(message))


def clone_message(message: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(message)


def consume_tool_images_after_llm(session: Session) -> int:
    consumed = 0
    for message in session.messages:
        if message.get("role") != "tool" or not message.get("images"):
            continue
        message["llm_images_consumed"] = True
        message.pop("images", None)
        consumed += 1
    return consumed
