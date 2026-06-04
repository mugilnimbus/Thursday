from __future__ import annotations

from typing import Any

from ...skills import SkillCatalog
from ..context import ToolContext


TOOL_NAME = "list_skills"
TOOL_ORDER = 49
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "List available Thursday skill package metadata from SKILL.md frontmatter. "
            "Use when deciding which skill to load or when the always-visible skill catalog may be stale. "
            "This returns names, what each skill does, when to use it, validated descriptions, "
            "and bundled resource paths, but not full skill bodies."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    catalog = SkillCatalog(context.config.skill_dir)
    return {
        "ok": True,
        "skills": catalog.catalog(),
        "message": "Skill metadata listed. Use load_skill for a specific skill body.",
    }
