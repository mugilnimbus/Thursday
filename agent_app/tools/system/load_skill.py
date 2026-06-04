from __future__ import annotations

from typing import Any

from ...skills import SkillCatalog
from ..context import ToolContext


TOOL_NAME = "load_skill"
TOOL_ORDER = 50
REQUIRES_CONTAINER = False

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Load a Thursday skill as a user-role conversation instruction. "
            "Use before performing a task when an available skill would teach the workflow, tools, "
            "failure recovery, and success criteria for that task type. "
            "This tool returns the full skill text; the orchestrator appends it to the conversation as a "
            "permanent user instruction message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill package name to load. Use a name from the skill catalog.",
                },
            },
            "required": ["skill_name"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    catalog = SkillCatalog(context.config.skill_dir)
    requested_name = str(args.get("skill_name") or "").strip()
    spec = catalog.get(requested_name)
    if not spec:
        return {
            "ok": False,
            "error": f"Unknown skill: {requested_name}",
            "available_skills": catalog.catalog(),
        }
    instruction_message = (
        f"[Thursday Loaded Skill: {spec.name}]\n\n"
        "The user is teaching you this skill so you can perform the current task correctly. "
        "Treat this as task operating instruction, not as a new user request.\n\n"
        f"{spec.body}"
    )
    return {
        "ok": True,
        "skill_name": spec.name,
        "description": spec.description,
        "skill_path": str(spec.path),
        "resources": spec.resources,
        "instruction_message": instruction_message,
        "loaded_as": "user_message",
        "message": f"Skill loaded: {spec.name}",
    }

