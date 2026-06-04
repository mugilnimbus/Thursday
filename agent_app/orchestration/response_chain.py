from __future__ import annotations

from typing import Any

from ..runtime.models import Session


def invalidate_response_chain(session: Session, reason: str) -> None:
    session.response_chain_valid = False
    session.last_response_id = ""
    session.response_anchor_message_count = 0
    session.response_chain_invalid_reason = reason


def response_chain_for_call(session: Session, settings: Any) -> tuple[str, int]:
    endpoint = str(settings.endpoint or "").rstrip("/")
    if transcript_contains_images(session.messages):
        invalidate_response_chain(session, "active transcript contains image attachments")
        return "", 0
    if not session.response_chain_valid or not session.last_response_id:
        return "", 0
    if session.response_chain_model != settings.model:
        invalidate_response_chain(session, "model changed since previous response")
        return "", 0
    if session.response_chain_endpoint.rstrip("/") != endpoint:
        invalidate_response_chain(session, "endpoint changed since previous response")
        return "", 0
    if session.response_anchor_message_count < 1 or session.response_anchor_message_count > len(session.messages):
        invalidate_response_chain(session, "response anchor was outside active message range")
        return "", 0
    return session.last_response_id, session.response_anchor_message_count


def update_response_chain_after_llm(session: Session, raw: dict[str, Any]) -> None:
    meta = raw.get("_thursday") if isinstance(raw.get("_thursday"), dict) else {}
    response_id = str(meta.get("response_id") or raw.get("id") or "")
    if not response_id or meta.get("api") != "responses":
        invalidate_response_chain(session, "LLM call did not return a Responses API id")
        return
    previous_failed = meta.get("previous_response_failed")
    session.last_response_id = response_id
    session.response_chain_valid = True
    session.response_anchor_message_count = len(session.messages)
    session.response_chain_model = session.settings.model
    session.response_chain_endpoint = session.settings.endpoint.rstrip("/")
    session.response_chain_invalid_reason = ""
    if previous_failed:
        session.add_event(
            "llm",
            "Previous response id was unavailable; rebuilt response chain from active transcript",
            previous_response_id=previous_failed.get("id"),
            error=previous_failed.get("error"),
            new_response_id=response_id,
        )


def transcript_contains_images(messages: list[dict[str, Any]]) -> bool:
    return any(bool(message.get("images")) for message in messages)
