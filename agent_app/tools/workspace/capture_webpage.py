from __future__ import annotations

import subprocess
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote_plus, urlparse

from ..context import (
    ToolContext,
    browser_diagnostics,
    find_browser_executable,
    run_headless_browser,
    workspace_relative_path,
)


TOOL_NAME = "capture_webpage"
TOOL_ORDER = 70
REQUIRES_CONTAINER = False

RESOLUTION_PRESETS = {
    "1080p": (1920, 1080),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Capture a high-resolution screenshot of a URL, local Windows HTML file, "
            "Docker workspace HTML file, or search results page with headless Chrome. Returns screenshot file paths "
            "and attaches the image for VLM inspection. Does not read DOM text, scrape "
            "visible text, or parse page content. Do not use this when the user asks to read file text/source "
            "directly, inspect file contents, or says not visually; use run_command with PowerShell Get-Content "
            "for Windows host paths instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "HTTP/HTTPS/file URL to screenshot, for example http://127.0.0.1:5000. "
                        "Optional if local_path, workspace_path, or query is provided."
                    ),
                },
                "local_path": {
                    "type": "string",
                    "description": (
                        "Absolute Windows host path to a local HTML file to screenshot, "
                        "for example C:\\Users\\Name\\Desktop\\app\\index.html. "
                        "Use this for files outside the Docker workspace."
                    ),
                },
                "workspace_path": {
                    "type": "string",
                    "description": (
                        "Workspace-relative HTML file to copy from Docker and screenshot as a static page. "
                        "Optional if url, local_path, or query is provided."
                    ),
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Search query to screenshot. Used only when url, local_path, and workspace_path are empty."
                    ),
                },
                "search_engine": {
                    "type": "string",
                    "description": "Search engine to use for query captures.",
                    "enum": ["duckduckgo", "google", "bing"],
                    "default": "google",
                },
                "resolution": {
                    "type": "string",
                    "description": (
                        "Screenshot size preset. Use 2k for normal visual QA and 4k when small details matter. "
                        "Explicit viewport_width and viewport_height override this preset."
                    ),
                    "enum": ["1080p", "2k", "4k"],
                    "default": "2k",
                },
                "viewport_width": {
                    "type": "integer",
                    "description": "Optional screenshot viewport width override. Maximum 3840.",
                },
                "viewport_height": {
                    "type": "integer",
                    "description": "Optional screenshot viewport height override. Maximum 2160.",
                },
                "wait_ms": {
                    "type": "integer",
                    "description": "Milliseconds to wait for rendering and JavaScript.",
                    "default": 1500,
                },
            },
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    url = str(args.get("url", "") or "").strip()
    local_path = str(args.get("local_path", "") or "").strip()
    workspace_path = str(args.get("workspace_path", "") or "").strip()
    query = str(args.get("query", "") or "").strip()
    search_engine = str(args.get("search_engine", "google") or "google").strip().lower()

    if not local_path and looks_like_windows_path(url):
        local_path = url
        url = ""

    if not url and not local_path and not workspace_path and not query:
        return {"ok": False, "error": "Provide url, local_path, workspace_path, or query"}

    provided_sources = [name for name, value in {
        "url": url,
        "local_path": local_path,
        "workspace_path": workspace_path,
        "query": query,
    }.items() if value]
    if len(provided_sources) > 1:
        return {
            "ok": False,
            "error": f"Provide exactly one capture source, got: {', '.join(provided_sources)}",
            "recovery_hint": "Call capture_webpage again with only one of url, local_path, workspace_path, or query.",
        }

    if looks_like_windows_path(workspace_path):
        return {
            "ok": False,
            "error": "workspace_path is for Docker workspace-relative paths, but this looks like a Windows host path.",
            "recovery_hint": "Call capture_webpage with local_path for absolute Windows paths such as C:\\Users\\...\\index.html.",
            "workspace_path": workspace_path,
        }

    browser = find_browser_executable(context.config)
    if not browser:
        return {
            "ok": False,
            "error": (
                "Chrome or Edge was not found. "
                "Set BROWSER_EXECUTABLE in .env to a Chromium-based browser path."
            ),
        }

    viewport = resolve_viewport(args)
    if "error" in viewport:
        return {"ok": False, "error": viewport["error"]}
    width = int(viewport["width"])
    height = int(viewport["height"])
    resolution = str(viewport["resolution"])
    wait = bounded_int(args.get("wait_ms", 1500), default=1500, minimum=0, maximum=10000, field_name="wait_ms")
    if isinstance(wait, dict):
        return wait
    target = url
    copied_dir = ""
    host_file_path = ""

    if local_path:
        local_file = Path(local_path).expanduser()
        if not local_file.exists() or not local_file.is_file():
            return {
                "ok": False,
                "error": f"Local file does not exist: {local_path}",
                "recovery_hint": "Use run_command with PowerShell to verify the host path, then call capture_webpage with the corrected local_path.",
                "local_path": local_path,
            }
        target = local_file.resolve().as_uri()
        host_file_path = str(local_file.resolve())

    if not target and query and not workspace_path:
        target = build_search_url(query=query, search_engine=search_engine)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    output_dir = context.config.visual_check_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"

    if workspace_path:
        target, copied_dir = copy_workspace_html(context, workspace_path, output_dir)
        if isinstance(target, dict):
            return target

    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https", "file"}:
        return {
            "ok": False,
            "error": "capture_webpage supports only http, https, and copied file URLs",
            "recovery_hint": "For absolute Windows host files, pass local_path. For Docker files, pass workspace_path. For websites, pass an http or https url.",
            "target": target,
        }

    timeout = max(15, int(wait / 1000) + 15)
    screenshot_result = run_headless_browser(
        browser,
        target,
        [
            "--screenshot=" + str(screenshot_path),
            f"--window-size={width},{height}",
            f"--virtual-time-budget={wait}",
        ],
        timeout=timeout,
    )

    stderr = screenshot_result.stderr or ""
    diagnostics = browser_diagnostics(stderr)

    llm_images: list[dict[str, str]] = []
    if screenshot_path.exists():
        llm_images.append(
            {
                "source": TOOL_NAME,
                "path": str(screenshot_path.resolve()),
                "mime_type": "image/png",
            }
        )

    return {
        "ok": screenshot_result.returncode == 0 and screenshot_path.exists(),
        "url": url,
        "local_path": host_file_path,
        "target": target,
        "query": query,
        "search_engine": search_engine if query and not url and not local_path and not workspace_path else "",
        "workspace_path": workspace_path,
        "copied_dir": copied_dir,
        "screenshot_path": str(screenshot_path.resolve()),
        "screenshot_exists": screenshot_path.exists(),
        "resolution": resolution,
        "viewport": {"width": width, "height": height},
        "diagnostics": diagnostics,
        "browser": browser,
        "exit_code": screenshot_result.returncode,
        "stderr": stderr.strip(),
        "llm_images": llm_images,
    }


def resolve_viewport(args: dict[str, Any]) -> dict[str, Any]:
    resolution = str(args.get("resolution", "2k") or "2k").strip().lower()
    width, height = RESOLUTION_PRESETS.get(resolution, RESOLUTION_PRESETS["2k"])
    if args.get("viewport_width") is not None:
        parsed_width = bounded_int(args.get("viewport_width"), default=width, minimum=320, maximum=3840, field_name="viewport_width")
        if isinstance(parsed_width, dict):
            return parsed_width
        width = parsed_width
        resolution = "custom"
    if args.get("viewport_height") is not None:
        parsed_height = bounded_int(args.get("viewport_height"), default=height, minimum=240, maximum=2160, field_name="viewport_height")
        if isinstance(parsed_height, dict):
            return parsed_height
        height = parsed_height
        resolution = "custom"
    return {
        "width": max(320, min(width, 3840)),
        "height": max(240, min(height, 2160)),
        "resolution": resolution if resolution in RESOLUTION_PRESETS or resolution == "custom" else "2k",
    }


def bounded_int(value: Any, *, default: int, minimum: int, maximum: int, field_name: str) -> int | dict[str, Any]:
    try:
        parsed = int(default if value is None or value == "" else value)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"{field_name} must be an integer"}
    return max(minimum, min(parsed, maximum))


def looks_like_windows_path(value: str) -> bool:
    stripped = str(value or "").strip()
    return len(stripped) >= 3 and stripped[1:3] in {":\\", ":/"} and stripped[0].isalpha()


def copy_workspace_html(context: ToolContext, workspace_path: str, output_dir: Path) -> tuple[str, str] | tuple[dict[str, Any], str]:
    rel_path = workspace_relative_path(context.config, workspace_path)
    parent = str(PurePosixPath(rel_path).parent)
    file_name = PurePosixPath(rel_path).name

    copied_root = output_dir / "workspace"
    copied_root.mkdir(parents=True, exist_ok=True)

    docker_source = (
        f"{context.config.docker_container_name}:"
        f"{context.config.docker_workdir.rstrip('/')}/{parent}"
    )
    copy_result = subprocess.run(
        ["docker", "cp", docker_source, str(copied_root)],
        text=True,
        capture_output=True,
        timeout=45,
    )

    if copy_result.returncode != 0:
        error = copy_result.stderr.strip() or f"Failed to copy {workspace_path} from Docker"
        return (
            {
                "ok": False,
                "error": error,
                "recovery_hint": (
                    "This path was treated as a Docker workspace path. If the original user file is on Windows "
                    "or starts with C:\\, call capture_webpage with local_path instead. Do not copy host files "
                    "into Docker unless the user explicitly asks."
                ),
                "workspace_path": workspace_path,
                "attempted_source": docker_source,
            },
            "",
        )

    local_parent = copied_root / PurePosixPath(parent).name if parent != "." else copied_root
    local_file = local_parent / file_name

    if not local_file.exists():
        return (
            {
                "ok": False,
                "error": f"Copied file was not found locally: {local_file}",
                "recovery_hint": (
                    "This path was treated as a Docker workspace path. If the user originally gave a Windows "
                    "host path or the file is outside Docker, call capture_webpage with local_path instead. "
                    "Only use workspace_path for exact files that already exist inside the Docker workspace."
                ),
                "workspace_path": workspace_path,
            },
            "",
        )

    return local_file.resolve().as_uri(), str(local_parent.resolve())


def build_search_url(query: str, search_engine: str) -> str:
    encoded = quote_plus(query)
    engine = (search_engine or "google").strip().lower()

    if engine == "google":
        return f"https://www.google.com/search?q={encoded}"

    if engine == "bing":
        return f"https://www.bing.com/search?q={encoded}"

    return f"https://duckduckgo.com/?q={encoded}"
