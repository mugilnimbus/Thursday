from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig


def render_system_prompt(config: AppConfig, tool_names: str, project_state: dict[str, str]) -> str:
    return render_prompt(
        config.prompt_dir / "thursday.md",
        {
            "agent_name": config.agent_name,
            "docker_container_name": config.docker_container_name,
            "docker_workdir": config.docker_workdir,
            "tool_names": tool_names,
            "workspace_label": f"docker://{config.docker_container_name}{config.docker_workdir}",
            **project_state,
        },
    )


def render_context_summary_prompt(config: AppConfig, summary: str) -> str:
    return render_prompt(config.prompt_dir / "context_summary.md", {"summary": summary})


def render_file_write_summary_prompt(config: AppConfig, path: str, content: str) -> str:
    return render_prompt(
        config.prompt_dir / "file_write_summary.md",
        {
            "path": path,
            "content": content,
            "summary_words": config.write_file_summary_max_tokens,
        },
    )


def render_conversation_summary_prompt(config: AppConfig, previous_summary: str, conversation: str) -> str:
    return render_prompt(
        config.prompt_dir / "conversation_summary.md",
        {
            "previous_summary": previous_summary or "none",
            "conversation": conversation,
        },
    )


def render_prompt(path: Path, values: dict[str, Any]) -> str:
    template = read_prompt(path)
    return template.format(**values).strip() + "\n"


def read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")
