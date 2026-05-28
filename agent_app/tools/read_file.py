from __future__ import annotations

from typing import Any

from .context import ToolContext, read_workspace_file

TOOL_NAME = "read_file"
TOOL_ORDER = 20
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Read the complete UTF-8 text content of a workspace file. Use this before editing an existing file or when you need exact source, config, or document content. Path must be relative to /workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path inside the container."},
            },
            "required": ["path"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return read_workspace_file(context, str(args["path"]))
