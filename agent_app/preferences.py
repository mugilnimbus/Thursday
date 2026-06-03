from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import AgentSettings


TOOL_NAME_MIGRATIONS = {
    "list_files": "run_command",
    "get_workspace_tree": "run_command",
    "search_files": "run_command",
    "search_google": "web_search",
    "visual_check_page": "capture_webpage",
    "inspect_webpage": "capture_webpage",
    "visual_webpage_search": "capture_webpage",
    "execute_command": "run_command",
}

REQUIRED_TOOLS = (
    "list_skills",
    "load_skill",
    "read_skill_resource",
    "get_current_datetime_location",
)


@dataclass
class DashboardPreferences:
    settings: AgentSettings
    enabled_tools: list[str]
    theme_color: str = "#9b63ff"

    def to_public(self, config: AppConfig, all_tools: list[str]) -> dict[str, Any]:
        default_settings = AgentSettings.from_config(config)
        default_settings.enabled_tools = list(all_tools)
        return {
            "settings": asdict(self.settings),
            "enabled_tools": self.enabled_tools,
            "theme": {"accent_color": self.theme_color},
            "required_tools": [name for name in REQUIRED_TOOLS if name in all_tools],
            "defaults": {
                "settings": asdict(default_settings),
                "enabled_tools": all_tools,
                "theme": {"accent_color": "#9b63ff"},
            },
        }


class PreferenceStore:
    def __init__(self, path: Path, config: AppConfig, all_tools: list[str]) -> None:
        self.path = path
        self.config = config
        self.all_tools = all_tools

    def load(self) -> DashboardPreferences:
        defaults = DashboardPreferences(AgentSettings.from_config(self.config), list(self.all_tools))
        defaults.settings.enabled_tools = list(self.all_tools)
        if not self.path.exists():
            return defaults
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults

        settings = AgentSettings.from_config(self.config)
        for key, value in (payload.get("settings") or {}).items():
            if hasattr(settings, key):
                setattr(settings, key, value)

        saved_tools = payload.get("enabled_tools", self.all_tools)
        known_tools = {TOOL_NAME_MIGRATIONS.get(str(name), str(name)) for name in payload.get("known_tools", saved_tools)}
        migrated_tools = [TOOL_NAME_MIGRATIONS.get(str(name), str(name)) for name in saved_tools]
        enabled_tools = []
        for name in migrated_tools:
            if name in self.all_tools and name not in enabled_tools:
                enabled_tools.append(name)
        for name in self.all_tools:
            if name not in known_tools and name not in enabled_tools:
                enabled_tools.append(name)
        enabled_tools = self.with_required_tools(enabled_tools)
        settings.enabled_tools = list(enabled_tools)
        theme_color = self.normalize_theme_color(
            (payload.get("theme") or {}).get("accent_color") if isinstance(payload.get("theme"), dict) else payload.get("theme_color")
        )
        return DashboardPreferences(settings=settings, enabled_tools=enabled_tools, theme_color=theme_color)

    def save(self, preferences: DashboardPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        enabled_tools = self.with_required_tools(preferences.enabled_tools)
        preferences.settings.enabled_tools = list(enabled_tools)
        payload = {
            "settings": asdict(preferences.settings),
            "enabled_tools": [name for name in enabled_tools if name in self.all_tools],
            "theme": {"accent_color": self.normalize_theme_color(preferences.theme_color)},
            "known_tools": list(self.all_tools),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def restore_defaults(self) -> DashboardPreferences:
        preferences = DashboardPreferences(AgentSettings.from_config(self.config), list(self.all_tools))
        preferences.settings.enabled_tools = list(self.all_tools)
        self.save(preferences)
        return preferences

    def with_required_tools(self, enabled_tools: list[str]) -> list[str]:
        normalized = [name for name in enabled_tools if name in self.all_tools]
        for name in REQUIRED_TOOLS:
            if name in self.all_tools and name not in normalized:
                normalized.append(name)
        return normalized

    def normalize_theme_color(self, value: Any) -> str:
        text = str(value or "#9b63ff").strip()
        if len(text) == 7 and text.startswith("#"):
            try:
                int(text[1:], 16)
                return text.lower()
            except ValueError:
                pass
        return "#9b63ff"
