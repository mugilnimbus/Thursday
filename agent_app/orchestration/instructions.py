from __future__ import annotations

from ..runtime.config import AppConfig
from ..runtime.models import Session
from ..skills import SkillCatalog
from .conversation_state import append_conversation_message
from .prompt_renderer import (
    render_operating_instructions_prompt,
    render_system_prompt,
    render_tool_operations_prompt,
)
from .response_chain import invalidate_response_chain


def system_prompt(config: AppConfig, session: Session) -> str:
    return render_system_prompt(config)


def operating_instructions_message(config: AppConfig) -> dict[str, str]:
    return {
        "role": "user",
        "content": render_operating_instructions_prompt(config),
    }


def tool_operations_message(config: AppConfig) -> dict[str, str]:
    catalog = SkillCatalog(config.skill_dir)
    return {
        "role": "user",
        "content": (
            f"{render_tool_operations_prompt(config)}\n"
            f"{catalog.catalog_markdown()}"
        ),
    }


def ensure_core_instruction_messages(config: AppConfig, session: Session) -> int:
    changed = 0
    changed += upsert_instruction_message(
        session,
        marker="[Thursday Operating Instructions]",
        message=operating_instructions_message(config),
        insert_at=1,
    )
    changed += upsert_instruction_message(
        session,
        marker="[Thursday Always Active Instructions: tool_operations]",
        message=tool_operations_message(config),
        insert_at=2,
    )
    if changed:
        invalidate_response_chain(session, "core operating instruction messages changed")
        session.add_event("skills", "Refreshed permanent operating instruction messages", count=changed)
    return changed


def upsert_instruction_message(session: Session, marker: str, message: dict[str, str], insert_at: int) -> int:
    for index, existing in enumerate(session.messages):
        if existing.get("role") == "user" and marker in str(existing.get("content") or ""):
            if existing.get("content") != message["content"]:
                session.messages[index] = message
                return 1
            return 0
    session.messages.insert(min(insert_at, len(session.messages)), message)
    return 1


def append_loaded_skill_message(
    session: Session,
    observation_name: str,
    observation_ok: bool,
    observation_args: dict[str, object],
    output: dict[str, object],
) -> bool:
    if observation_name != "load_skill" or not observation_ok:
        return False
    content = output.get("instruction_message")
    if not isinstance(content, str) or not content.strip():
        return False
    skill_name = str(output.get("skill_name") or observation_args.get("skill_name") or "").strip()
    marker = f"[Thursday Loaded Skill: {skill_name}]"
    if skill_name and any(
        message.get("role") == "user" and marker in str(message.get("content") or "")
        for message in session.messages
    ):
        return False
    append_conversation_message(session, {"role": "user", "content": content})
    return True
