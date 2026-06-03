from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..context import ToolContext

TOOL_NAME = "run_command"
TOOL_ORDER = 80
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Run a command on the Windows host using PowerShell or cmd. "
            "This tool does not choose Docker, host, or any workspace automatically. "
            "If the task is inside the Docker workspace, include the full docker exec command yourself. "
            "Use this for direct Windows host file reads, source inspection, and non-visual file checks; "
            "for example Get-Content -Raw -LiteralPath 'C:\\path\\file.html'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command text to run on the Windows host. Include docker exec explicitly when needed.",
                },
                "shell": {
                    "type": "string",
                    "enum": ["powershell", "cmd"],
                    "description": "Windows shell to use.",
                    "default": "powershell",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional host working directory. Defaults to the Thursday app root.",
                },
                "timeout_seconds": {"type": "integer", "description": "Timeout in seconds.", "default": 30},
            },
            "required": ["command"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    command = str(args["command"])
    shell = str(args.get("shell") or "powershell").strip().lower()
    cwd = resolve_cwd(context, str(args.get("cwd") or ""))
    timeout = max(1, min(int(args.get("timeout_seconds", 30)), context.config.docker_command_timeout_seconds))

    if shell == "cmd":
        process_args = ["cmd.exe", "/d", "/s", "/c", command]
    else:
        shell = "powershell"
        process_args = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]

    try:
        result = subprocess.run(
            process_args,
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "shell": shell,
            "cwd": str(cwd),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "error": f"Command timed out after {timeout} seconds.",
            "shell": shell,
            "cwd": str(cwd),
        }


def resolve_cwd(context: ToolContext, value: str) -> Path:
    if not value:
        return context.config.root_dir
    path = Path(value)
    if not path.is_absolute():
        path = context.config.root_dir / path
    return path.resolve()
