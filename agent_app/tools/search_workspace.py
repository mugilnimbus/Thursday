from __future__ import annotations

from typing import Any

from .context import ToolContext, docker_exec, quote_shell, workspace_relative_path

TOOL_NAME = "search_workspace"
TOOL_ORDER = 50
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Search all text files in the Docker workspace for a literal string. Use when locating code, symbols, text, TODOs, errors, or file references before reading/editing. Query is literal, not regex.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Literal text to search for."},
                "directory": {"type": "string", "description": "Workspace-relative directory inside the container.", "default": "."},
            },
            "required": ["query"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args["query"])
    directory = workspace_relative_path(context.config, str(args.get("directory", ".")))
    result = docker_exec(
        context.config,
        (
            f"test -d {quote_shell(directory)} || exit 66\n"
            f"grep -RIn --binary-files=without-match -- {quote_shell(query)} {quote_shell(directory)} 2>/dev/null"
        ),
        timeout_seconds=45,
    )
    if result.returncode not in (0, 1):
        return {"ok": False, "error": result.stderr.strip() or "Search failed"}

    matches: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        file_path, line_number, preview = parts
        if file_path.startswith("./"):
            file_path = file_path[2:]
        matches.append(
            {
                "path": file_path,
                "line": int(line_number) if line_number.isdigit() else 0,
                "preview": preview.strip(),
            }
        )
    return {"ok": True, "matches": matches, "count": len(matches), "container": context.config.docker_container_name}
