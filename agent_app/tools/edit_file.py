from __future__ import annotations

import difflib
from typing import Any

from .context import ToolContext, read_workspace_file, workspace_relative_path, write_workspace_file

TOOL_NAME = "edit_file"
TOOL_ORDER = 40
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Modify an existing UTF-8 file by replacing exact text and returning a unified diff. Use after read_file when you know the exact old_text. Use expected_replacements=1 for one targeted edit or 0 to replace every occurrence.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative file path inside the container."},
                "old_text": {"type": "string", "description": "Exact text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "expected_replacements": {
                    "type": "integer",
                    "description": "Expected number of replacements. Use 0 to replace every occurrence.",
                    "default": 1,
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace_relative_path(context.config, str(args["path"]))
    old_text = str(args["old_text"])
    new_text = str(args["new_text"])
    expected_replacements = int(args.get("expected_replacements", 1))
    if old_text == "":
        return {"ok": False, "error": "old_text cannot be empty"}

    read_result = read_workspace_file(context, path)
    if not read_result.get("ok"):
        return read_result

    original = str(read_result.get("content", ""))
    occurrences = original.count(old_text)
    expected = max(0, expected_replacements)
    if occurrences == 0:
        return {"ok": False, "error": f"Text to replace was not found in {path}"}
    if expected and occurrences != expected:
        return {
            "ok": False,
            "error": f"Expected {expected} replacement(s), but found {occurrences} occurrence(s) in {path}",
            "occurrences": occurrences,
        }

    updated = original.replace(old_text, new_text) if expected == 0 else original.replace(old_text, new_text, expected)
    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    write_result = write_workspace_file(context, path, updated)
    if not write_result.get("ok"):
        return write_result
    return {
        "ok": True,
        "path": path,
        "replacements": occurrences if expected == 0 else expected,
        "diff": diff,
        "bytes": write_result.get("bytes", 0),
        "container": context.config.docker_container_name,
    }
