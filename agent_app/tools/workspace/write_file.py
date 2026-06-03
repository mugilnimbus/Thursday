from __future__ import annotations

from typing import Any

from ..context import ToolContext, write_workspace_file

TOOL_NAME = "write_file"
TOOL_ORDER = 30
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Create or fully overwrite a UTF-8 text file in the Docker workspace. Use for new files or complete "
            "replacements. Provide the full desired file content. Parent folders are created automatically. "
            "Path must be relative to /workspace, not a Windows host path. For C:\\ host paths, use run_command "
            "with PowerShell instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path inside the container, for example app/index.html. Do not pass C:\\ paths.",
                },
                "content": {"type": "string", "description": "Complete file contents to write."},
            },
            "required": ["path", "content"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return write_workspace_file(context, str(args["path"]), str(args["content"]))
