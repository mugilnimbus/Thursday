from __future__ import annotations

from typing import Any

from ...skills import SkillCatalog
from ..context import ToolContext


TOOL_NAME = "read_skill_resource"
TOOL_ORDER = 51
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Read one bundled resource from a loaded Thursday skill package. "
            "Use only when the loaded skill mentions a specific references/, scripts/, or assets/ file that is needed for the current step. "
            "This supports progressive disclosure: load the skill first, then load only the resource you need."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill package name that owns the resource.",
                },
                "resource_path": {
                    "type": "string",
                    "description": "Relative resource path starting with references/, scripts/, or assets/.",
                },
            },
            "required": ["skill_name", "resource_path"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    catalog = SkillCatalog(context.config.skill_dir)
    skill_name = str(args.get("skill_name") or "").strip()
    resource_path = str(args.get("resource_path") or "").strip()
    try:
        return {"ok": True, **catalog.read_resource(skill_name, resource_path)}
    except Exception as exc:
        spec = catalog.get(skill_name)
        return {
            "ok": False,
            "error": str(exc),
            "available_resources": spec.resources if spec else [],
        }
