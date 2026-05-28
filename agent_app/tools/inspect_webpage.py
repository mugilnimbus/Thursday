from __future__ import annotations

import subprocess
import uuid
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

from .context import (
    ToolContext,
    browser_diagnostics,
    find_browser_executable,
    run_headless_browser,
    workspace_relative_path,
)
from .parsers import PageSummaryParser

TOOL_NAME = "inspect_webpage"
TOOL_ORDER = 70
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Visually inspect a webpage or workspace HTML file with headless Chrome. Use after building/changing web pages, or when an exact URL needs page title, visible text, screenshot path, and browser diagnostics.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "HTTP/HTTPS URL to inspect, for example http://127.0.0.1:5000. Optional if workspace_path is provided.",
                },
                "workspace_path": {
                    "type": "string",
                    "description": "Workspace-relative HTML file to copy from Docker and inspect as a static page. Optional if url is provided.",
                },
                "viewport_width": {"type": "integer", "description": "Screenshot viewport width.", "default": 1280},
                "viewport_height": {"type": "integer", "description": "Screenshot viewport height.", "default": 800},
                "wait_ms": {"type": "integer", "description": "Milliseconds to wait for rendering and JavaScript.", "default": 1500},
            },
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url", ""))
    workspace_path = str(args.get("workspace_path", ""))
    if not url and not workspace_path:
        return {"ok": False, "error": "Provide either url or workspace_path"}

    browser = find_browser_executable(context.config)
    if not browser:
        return {
            "ok": False,
            "error": "Chrome or Edge was not found. Set BROWSER_EXECUTABLE in .env to a Chromium-based browser path.",
        }

    width = max(320, min(int(args.get("viewport_width", 1280)), 3840))
    height = max(240, min(int(args.get("viewport_height", 800)), 2160))
    wait = max(0, min(int(args.get("wait_ms", 1500)), 10000))
    target = url.strip()
    copied_dir = ""

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    output_dir = context.config.visual_check_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"

    if workspace_path:
        rel_path = workspace_relative_path(context.config, workspace_path)
        parent = str(PurePosixPath(rel_path).parent)
        file_name = PurePosixPath(rel_path).name
        copied_root = output_dir / "workspace"
        copied_root.mkdir(parents=True, exist_ok=True)
        docker_source = f"{context.config.docker_container_name}:{context.config.docker_workdir.rstrip('/')}/{parent}"
        copy_result = subprocess.run(
            ["docker", "cp", docker_source, str(copied_root)],
            text=True,
            capture_output=True,
            timeout=45,
        )
        if copy_result.returncode != 0:
            return {"ok": False, "error": copy_result.stderr.strip() or f"Failed to copy {workspace_path} from Docker"}
        local_parent = copied_root / PurePosixPath(parent).name if parent != "." else copied_root
        local_file = local_parent / file_name
        if not local_file.exists():
            return {"ok": False, "error": f"Copied file was not found locally: {local_file}"}
        target = local_file.resolve().as_uri()
        copied_dir = str(local_parent.resolve())

    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https", "file"}:
        return {"ok": False, "error": "inspect_webpage supports only http, https, and copied file URLs"}

    timeout = max(15, int(wait / 1000) + 15)
    screenshot_result = run_headless_browser(
        browser,
        target,
        ["--screenshot=" + str(screenshot_path), f"--window-size={width},{height}", f"--virtual-time-budget={wait}"],
        timeout=timeout,
    )
    dom_result = run_headless_browser(
        browser,
        target,
        ["--dump-dom", f"--window-size={width},{height}", f"--virtual-time-budget={wait}"],
        timeout=timeout,
    )

    parser = PageSummaryParser()
    parser.feed(dom_result.stdout or "")
    stderr = "\n".join(part for part in [screenshot_result.stderr, dom_result.stderr] if part)
    diagnostics = browser_diagnostics(stderr)

    return {
        "ok": screenshot_result.returncode == 0 and screenshot_path.exists(),
        "url": url or "",
        "target": target,
        "workspace_path": workspace_path or "",
        "copied_dir": copied_dir,
        "screenshot_path": str(screenshot_path.resolve()),
        "screenshot_exists": screenshot_path.exists(),
        "viewport": {"width": width, "height": height},
        "title": parser.title,
        "visible_text": parser.visible_text(),
        "diagnostics": diagnostics,
        "browser": browser,
        "exit_code": screenshot_result.returncode,
        "stderr": stderr.strip(),
    }
