from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import AppConfig
from ..reminders import ReminderStore


@dataclass
class ToolContext:
    config: AppConfig
    reminder_store: ReminderStore | None = None

    def workspace_label(self) -> str:
        return f"docker://{self.config.docker_container_name}{self.config.docker_workdir}"


def quote_shell(value: str) -> str:
    return shlex.quote(value)


def workspace_relative_path(config: AppConfig, value: str) -> str:
    normalized = str(value or ".").replace("\\", "/").strip()
    workdir = config.docker_workdir.rstrip("/") or "/workspace"
    if normalized == workdir or normalized == f"{workdir}/":
        return "."
    if normalized.startswith(f"{workdir}/"):
        normalized = normalized[len(workdir) + 1 :]
    if normalized in {"", "."}:
        return "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"..", ""} for part in path.parts):
        raise ValueError("Path must stay inside the Docker workspace")
    return path.as_posix()


def container_running(config: AppConfig) -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", config.docker_container_name],
            text=True,
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def docker_not_running_error(config: AppConfig) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"Docker container '{config.docker_container_name}' is not running. "
            f"Start it with: docker run -dit --name {config.docker_container_name} "
            f"-w {config.docker_workdir} {config.docker_image} bash"
        ),
    }


def docker_exec(
    config: AppConfig,
    script: str,
    timeout_seconds: int,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    wrapped = (
        "set -euo pipefail\n"
        f"mkdir -p {quote_shell(config.docker_workdir)}\n"
        f"cd {quote_shell(config.docker_workdir)}\n"
        f"{script}"
    )
    return subprocess.run(
        ["docker", "exec", "-i", config.docker_container_name, "bash", "-lc", wrapped],
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def read_workspace_file(context: ToolContext, path: str) -> dict[str, Any]:
    rel_path = workspace_relative_path(context.config, path)
    result = docker_exec(
        context.config,
        (
            f"test -f {quote_shell(rel_path)} || exit 66\n"
            f"wc -c < {quote_shell(rel_path)}\n"
            "printf '\\n__THURSDAY_CONTENT__\\n'\n"
            f"cat {quote_shell(rel_path)}"
        ),
        timeout_seconds=30,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"File does not exist: {rel_path}"}
    if "\n__THURSDAY_CONTENT__\n" not in result.stdout:
        return {"ok": False, "error": "Unexpected read_file output from Docker"}
    byte_count, content = result.stdout.split("\n__THURSDAY_CONTENT__\n", 1)
    return {
        "ok": True,
        "path": rel_path,
        "content": content,
        "bytes": int(byte_count.strip() or "0"),
        "container": context.config.docker_container_name,
    }


def write_workspace_file(context: ToolContext, path: str, content: str) -> dict[str, Any]:
    rel_path = workspace_relative_path(context.config, path)
    parent = str(PurePosixPath(rel_path).parent)
    result = docker_exec(
        context.config,
        f"mkdir -p {quote_shell(parent)}\ncat > {quote_shell(rel_path)}\nwc -c < {quote_shell(rel_path)}",
        timeout_seconds=30,
        input_text=content,
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"Failed to write: {rel_path}"}
    return {
        "ok": True,
        "path": rel_path,
        "bytes": int(result.stdout.strip() or "0"),
        "container": context.config.docker_container_name,
    }


def find_browser_executable(config: AppConfig) -> str:
    configured = config.browser_executable.strip()
    candidates = [
        configured,
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def run_headless_browser(
    browser: str,
    target: str,
    args: list[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    command = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-dev-shm-usage",
        *args,
        target,
    ]
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")


def browser_diagnostics(stderr: str) -> list[str]:
    diagnostics: list[str] = []
    for line in stderr.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("error", "failed", "not found", "exception", "refused")):
            diagnostics.append(line.strip())
    return diagnostics
