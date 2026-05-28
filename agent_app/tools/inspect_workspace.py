from __future__ import annotations

from typing import Any

from .context import ToolContext, docker_exec, quote_shell, workspace_relative_path

TOOL_NAME = "inspect_workspace"
TOOL_ORDER = 10
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Inspect the Docker workspace before reading or editing projects. Returns a directory tree and all files under a workspace-relative directory. Use directory='.' for /workspace, pattern to filter filenames, and max_depth to control tree depth.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Workspace-relative directory inside the container.", "default": "."},
                "pattern": {"type": "string", "description": "Optional filename text filter for the file list.", "default": ""},
                "max_depth": {"type": "integer", "description": "Maximum tree depth.", "default": 3},
            },
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    directory = workspace_relative_path(context.config, str(args.get("directory", ".")))
    pattern = str(args.get("pattern", ""))
    max_depth = max(1, min(int(args.get("max_depth", 3)), 10))
    script = (
        f"test -d {quote_shell(directory)} || exit 66\n"
        "printf '__THURSDAY_TREE__\\n'\n"
        f"find {quote_shell(directory)} -maxdepth {max_depth} -print | sed 's#^./##' | sed '/^$/d'\n"
        "printf '__THURSDAY_FILES__\\n'\n"
        f"find {quote_shell(directory)} -type f -print | sed 's#^./##'"
    )
    result = docker_exec(context.config, script, timeout_seconds=30)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"Directory does not exist: {directory}"}
    if "__THURSDAY_TREE__\n" not in result.stdout or "\n__THURSDAY_FILES__\n" not in result.stdout:
        return {"ok": False, "error": "Unexpected inspect_workspace output from Docker"}

    tree_part, files_part = result.stdout.split("\n__THURSDAY_FILES__\n", 1)
    tree = tree_part.replace("__THURSDAY_TREE__\n", "").strip() or "(empty workspace)"
    files = [line.strip() for line in files_part.splitlines() if line.strip()]
    if pattern:
        lowered = pattern.lower()
        files = [item for item in files if lowered in item.lower()]
    return {
        "ok": True,
        "directory": directory,
        "tree": tree,
        "files": files,
        "count": len(files),
        "container": context.config.docker_container_name,
    }
