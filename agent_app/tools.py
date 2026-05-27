from __future__ import annotations

import difflib
import shlex
import subprocess
import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .config import AppConfig


class ToolRegistry:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.container_tools = {
            "inspect_workspace",
            "read_file",
            "write_file",
            "edit_file",
            "search_workspace",
            "run_command",
        }
        self.tools = {
            "inspect_workspace": self.inspect_workspace,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "search_workspace": self.search_workspace,
            "web_search": self.web_search,
            "inspect_webpage": self.inspect_webpage,
            "run_command": self.run_command,
        }

    def names(self) -> list[str]:
        return list(self.tools.keys())

    def workspace_label(self) -> str:
        return f"docker://{self.config.docker_container_name}{self.config.docker_workdir}"

    def definitions(self, enabled_tools: list[str] | None = None) -> list[dict[str, Any]]:
        enabled = set(self.names() if enabled_tools is None else enabled_tools)
        definitions = [
            {
                "type": "function",
                "function": {
                    "name": "inspect_workspace",
                    "description": "Inspect the Ubuntu Docker workspace by returning files and a compact tree in one result.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directory": {"type": "string", "description": "Workspace-relative directory inside the container.", "default": "."},
                            "pattern": {"type": "string", "description": "Optional filename text filter for the file list.", "default": ""},
                            "max_depth": {"type": "integer", "description": "Maximum tree depth.", "default": 3},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a UTF-8 text file from the Ubuntu Docker workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Workspace-relative file path inside the container."},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create or overwrite a UTF-8 text file in the Ubuntu Docker workspace.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Workspace-relative file path inside the container."},
                            "content": {"type": "string", "description": "Complete file contents to write."},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Modify an existing UTF-8 text file by replacing exact text and return a unified diff.",
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
            },
            {
                "type": "function",
                "function": {
                    "name": "search_workspace",
                    "description": "Search text files in the Ubuntu Docker workspace for a literal string.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Literal text to search for."},
                            "directory": {"type": "string", "description": "Workspace-relative directory inside the container.", "default": "."},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web for current documentation, library usage, and software-building references. Returns titles and links.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Web search query."},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_webpage",
                    "description": "Open a webpage in headless Chrome, save a screenshot, and return page title, visible text, and diagnostics.",
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
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a Bash command inside the Ubuntu Docker workspace and return stdout, stderr, and exit code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Bash command to execute inside /workspace."},
                            "timeout_seconds": {"type": "integer", "description": "Timeout in seconds.", "default": 30},
                        },
                        "required": ["command"],
                    },
                },
            },
        ]
        return [item for item in definitions if item["function"]["name"] in enabled]

    def invoke(self, name: str, args: dict[str, Any], enabled_tools: list[str] | None = None) -> dict[str, Any]:
        if enabled_tools is not None and name not in enabled_tools:
            return {"ok": False, "error": f"Tool is disabled: {name}"}
        if name not in self.tools:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        if name in self.container_tools and not self.container_running():
            return {
                "ok": False,
                "error": (
                    f"Docker container '{self.config.docker_container_name}' is not running. "
                    f"Start it with: docker run -dit --name {self.config.docker_container_name} "
                    f"-w {self.config.docker_workdir} {self.config.docker_image} bash"
                ),
            }
        try:
            return self.tools[name](**args)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def container_running(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", self.config.docker_container_name],
                text=True,
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0 and result.stdout.strip().lower() == "true"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def inspect_workspace(self, directory: str = ".", pattern: str = "", max_depth: int = 3, max_results: int | None = None) -> dict[str, Any]:
        directory = self._relative_path(directory)
        max_depth = max(1, min(int(max_depth), 10))
        script = (
            f"test -d {self._q(directory)} || exit 66\n"
            "printf '__THURSDAY_TREE__\\n'\n"
            f"find {self._q(directory)} -maxdepth {max_depth} -print | sed 's#^./##' | sed '/^$/d'\n"
            "printf '__THURSDAY_FILES__\\n'\n"
            f"find {self._q(directory)} -type f -print | sed 's#^./##'"
        )
        result = self._docker_exec(
            script,
            timeout_seconds=30,
        )
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
            "container": self.config.docker_container_name,
        }

    def read_file(self, path: str, max_chars: int | None = None) -> dict[str, Any]:
        path = self._relative_path(path)
        result = self._docker_exec(
            f"test -f {self._q(path)} || exit 66\nwc -c < {self._q(path)}\nprintf '\\n__THURSDAY_CONTENT__\\n'\ncat {self._q(path)}",
            timeout_seconds=30,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or f"File does not exist: {path}"}
        if "\n__THURSDAY_CONTENT__\n" not in result.stdout:
            return {"ok": False, "error": "Unexpected read_file output from Docker"}
        byte_count, content = result.stdout.split("\n__THURSDAY_CONTENT__\n", 1)
        size = int(byte_count.strip() or "0")
        return {
            "ok": True,
            "path": path,
            "content": content,
            "bytes": size,
            "container": self.config.docker_container_name,
        }

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        path = self._relative_path(path)
        parent = str(PurePosixPath(path).parent)
        result = self._docker_exec(
            f"mkdir -p {self._q(parent)}\ncat > {self._q(path)}\nwc -c < {self._q(path)}",
            timeout_seconds=30,
            input_text=content,
        )
        if result.returncode != 0:
            return {"ok": False, "error": result.stderr.strip() or f"Failed to write: {path}"}
        return {
            "ok": True,
            "path": path,
            "bytes": int(result.stdout.strip() or "0"),
            "container": self.config.docker_container_name,
        }

    def edit_file(self, path: str, old_text: str, new_text: str, expected_replacements: int = 1) -> dict[str, Any]:
        path = self._relative_path(path)
        if old_text == "":
            return {"ok": False, "error": "old_text cannot be empty"}

        read_result = self.read_file(path)
        if not read_result.get("ok"):
            return read_result

        original = str(read_result.get("content", ""))
        occurrences = original.count(old_text)
        expected = max(0, int(expected_replacements))
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
        write_result = self.write_file(path, updated)
        if not write_result.get("ok"):
            return write_result
        return {
            "ok": True,
            "path": path,
            "replacements": occurrences if expected == 0 else expected,
            "diff": diff,
            "bytes": write_result.get("bytes", 0),
            "container": self.config.docker_container_name,
        }

    def search_workspace(self, query: str, directory: str = ".", max_results: int | None = None) -> dict[str, Any]:
        directory = self._relative_path(directory)
        result = self._docker_exec(
            f"test -d {self._q(directory)} || exit 66\ngrep -RIn --binary-files=without-match -- {self._q(query)} {self._q(directory)} 2>/dev/null",
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
            matches.append({
                "path": file_path,
                "line": int(line_number) if line_number.isdigit() else 0,
                "preview": preview.strip(),
            })
        return {"ok": True, "matches": matches, "count": len(matches), "container": self.config.docker_container_name}

    def web_search(self, query: str, max_results: int | None = None) -> dict[str, Any]:
        with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "ThursdayAgent/1.0"}) as client:
            response = client.get("https://lite.duckduckgo.com/lite/", params={"q": query})
            response.raise_for_status()

        parser = _SearchResultParser()
        parser.feed(response.text)
        return {
            "ok": True,
            "query": query,
            "results": parser.results,
            "count": len(parser.results),
            "source": "DuckDuckGo Lite",
        }

    def inspect_webpage(
        self,
        url: str = "",
        workspace_path: str = "",
        viewport_width: int = 1280,
        viewport_height: int = 800,
        wait_ms: int = 1500,
    ) -> dict[str, Any]:
        if not url and not workspace_path:
            return {"ok": False, "error": "Provide either url or workspace_path"}

        browser = self._find_browser_executable()
        if not browser:
            return {
                "ok": False,
                "error": "Chrome or Edge was not found. Set BROWSER_EXECUTABLE in .env to a Chromium-based browser path.",
            }

        width = max(320, min(int(viewport_width), 3840))
        height = max(240, min(int(viewport_height), 2160))
        wait = max(0, min(int(wait_ms), 10000))
        target = url.strip()
        copied_dir = ""

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
        output_dir = self.config.visual_check_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = output_dir / "screenshot.png"

        if workspace_path:
            rel_path = self._relative_path(workspace_path)
            parent = str(PurePosixPath(rel_path).parent)
            file_name = PurePosixPath(rel_path).name
            copied_root = output_dir / "workspace"
            copied_root.mkdir(parents=True, exist_ok=True)
            docker_source = f"{self.config.docker_container_name}:{self.config.docker_workdir.rstrip('/')}/{parent}"
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
        screenshot_result = self._run_headless_browser(
            browser,
            target,
            ["--screenshot=" + str(screenshot_path), f"--window-size={width},{height}", f"--virtual-time-budget={wait}"],
            timeout=timeout,
        )
        dom_result = self._run_headless_browser(
            browser,
            target,
            ["--dump-dom", f"--window-size={width},{height}", f"--virtual-time-budget={wait}"],
            timeout=timeout,
        )

        parser = _PageSummaryParser()
        parser.feed(dom_result.stdout or "")
        stderr = "\n".join(part for part in [screenshot_result.stderr, dom_result.stderr] if part)
        diagnostics = self._browser_diagnostics(stderr)

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

    def run_command(self, command: str, timeout_seconds: int = 30) -> dict[str, Any]:
        lowered = command.lower()
        blocked = ["docker ", "powershell", "rm -rf /", "mkfs", "format ", ":(){", "shutdown", "reboot"]
        if any(pattern in lowered for pattern in blocked):
            return {"ok": False, "error": "Blocked command pattern for the Docker workspace."}
        timeout = max(1, min(int(timeout_seconds), self.config.docker_command_timeout_seconds))
        result = self._docker_exec(command, timeout_seconds=timeout)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "container": self.config.docker_container_name,
            "workdir": self.config.docker_workdir,
        }

    def _docker_exec(self, script: str, timeout_seconds: int, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        wrapped = f"set -euo pipefail\nmkdir -p {self._q(self.config.docker_workdir)}\ncd {self._q(self.config.docker_workdir)}\n{script}"
        return subprocess.run(
            ["docker", "exec", "-i", self.config.docker_container_name, "bash", "-lc", wrapped],
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )

    def _relative_path(self, value: str) -> str:
        normalized = str(value or ".").replace("\\", "/").strip()
        workdir = self.config.docker_workdir.rstrip("/") or "/workspace"
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

    def context_sized_limit(self, value: int | None) -> int:
        if value is None:
            return max(1, self.config.default_context_window)
        return max(1, min(int(value), self.config.default_context_window))

    def _q(self, value: str) -> str:
        return shlex.quote(value)

    def _find_browser_executable(self) -> str:
        configured = self.config.browser_executable.strip()
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

    def _run_headless_browser(self, browser: str, target: str, args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
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

    def _browser_diagnostics(self, stderr: str) -> list[str]:
        diagnostics: list[str] = []
        for line in stderr.splitlines():
            lowered = line.lower()
            if any(token in lowered for token in ("error", "failed", "not found", "exception", "refused")):
                diagnostics.append(line.strip())
        return diagnostics


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._in_link = False
        self._seen_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        if not href:
            return
        self._href = href
        self._text = []
        self._in_link = True

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._in_link:
            return
        title = " ".join("".join(self._text).split())
        url = self._normalize_url(self._href)
        self._href = ""
        self._text = []
        self._in_link = False
        if not title or not url or url in self._seen_urls:
            return
        self._seen_urls.add(url)
        self.results.append({"title": title, "url": url})

    def _normalize_url(self, href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return unquote(query["uddg"][0])
        if parsed.scheme in {"http", "https"} and "duckduckgo.com" not in parsed.netloc:
            return href
        return ""


class _PageSummaryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._hidden_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in {"script", "style", "noscript", "svg", "head"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg", "head"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
            return
        if not self._hidden_depth:
            self._text_parts.append(text)

    def visible_text(self) -> str:
        return "\n".join(self._text_parts)
