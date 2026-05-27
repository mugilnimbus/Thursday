from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .models import AgentSettings


TOOL_NAME_MIGRATIONS = {
    "list_files": "inspect_workspace",
    "get_workspace_tree": "inspect_workspace",
    "search_files": "search_workspace",
    "search_google": "web_search",
    "visual_check_page": "inspect_webpage",
    "execute_command": "run_command",
}


@dataclass
class DashboardPreferences:
    settings: AgentSettings
    enabled_tools: list[str]

    def to_public(self, config: AppConfig, all_tools: list[str]) -> dict[str, Any]:
        default_settings = AgentSettings.from_config(config)
        default_settings.enabled_tools = list(all_tools)
        return {
            "settings": asdict(self.settings),
            "enabled_tools": self.enabled_tools,
            "defaults": {
                "settings": asdict(default_settings),
                "enabled_tools": all_tools,
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
        known_tools = {str(name) for name in payload.get("known_tools", saved_tools)}
        migrated_tools = [TOOL_NAME_MIGRATIONS.get(str(name), str(name)) for name in saved_tools]
        enabled_tools = []
        for name in migrated_tools:
            if name in self.all_tools and name not in enabled_tools:
                enabled_tools.append(name)
        for name in self.all_tools:
            if name not in known_tools and name not in enabled_tools:
                enabled_tools.append(name)
        settings.enabled_tools = list(enabled_tools)
        return DashboardPreferences(settings=settings, enabled_tools=enabled_tools)

    def save(self, preferences: DashboardPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "settings": asdict(preferences.settings),
            "enabled_tools": [name for name in preferences.enabled_tools if name in self.all_tools],
            "known_tools": list(self.all_tools),
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def restore_defaults(self) -> DashboardPreferences:
        preferences = DashboardPreferences(AgentSettings.from_config(self.config), list(self.all_tools))
        preferences.settings.enabled_tools = list(self.all_tools)
        self.save(preferences)
        return preferences
