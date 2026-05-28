from __future__ import annotations

from typing import Any

from .context import ToolContext, docker_exec

TOOL_NAME = "run_command"
TOOL_ORDER = 80
REQUIRES_CONTAINER = True
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Run a Bash command inside /workspace of the Ubuntu Docker container. Use for installs, tests, builds, scripts, file inspection, package checks, and app servers. Returns full stdout, stderr, and exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to execute inside /workspace."},
                "timeout_seconds": {"type": "integer", "description": "Timeout in seconds.", "default": 30},
            },
            "required": ["command"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args["command"])
    lowered = command.lower()
    blocked = ["docker ", "powershell", "rm -rf /", "mkfs", "format ", ":(){", "shutdown", "reboot"]
    if any(pattern in lowered for pattern in blocked):
        return {"ok": False, "error": "Blocked command pattern for the Docker workspace."}
    timeout = max(1, min(int(args.get("timeout_seconds", 30)), context.config.docker_command_timeout_seconds))
    result = docker_exec(context.config, command, timeout_seconds=timeout)
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "container": context.config.docker_container_name,
        "workdir": context.config.docker_workdir,
    }
