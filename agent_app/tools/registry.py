from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..reminders import ReminderStore
from .base import ToolSpec
from .context import ToolContext, container_running, docker_not_running_error


INTERNAL_MODULES = {"base", "context", "parsers", "registry"}


class ToolRegistry:
    """Auto-discovers tools from python files in agent_app/tools."""

    def __init__(self, config: AppConfig, reminder_store: ReminderStore | None = None) -> None:
        self.config = config
        self.context = ToolContext(config=config, reminder_store=reminder_store)
        self.package_dir = Path(__file__).parent
        self.load_errors: dict[str, str] = {}
        self._snapshot: tuple[tuple[str, int, int], ...] = ()
        self.tool_specs = self._load_tool_specs()

    def names(self) -> list[str]:
        self._refresh_if_changed()
        return list(self.tool_specs.keys())

    def workspace_label(self) -> str:
        return self.context.workspace_label()

    def definitions(self, enabled_tools: list[str] | None = None) -> list[dict[str, Any]]:
        self._refresh_if_changed()
        enabled = set(self.names() if enabled_tools is None else enabled_tools)
        return [spec.definition for spec in self.tool_specs.values() if spec.name in enabled]

    def invoke(self, name: str, args: dict[str, Any], enabled_tools: list[str] | None = None) -> dict[str, Any]:
        self._refresh_if_changed()
        if enabled_tools is not None and name not in enabled_tools:
            return {"ok": False, "error": f"Tool is disabled: {name}"}
        spec = self.tool_specs.get(name)
        if not spec:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        if spec.requires_container and not container_running(self.config):
            return docker_not_running_error(self.config)
        try:
            return spec.runner(self.context, args or {})
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _load_tool_specs(self) -> dict[str, ToolSpec]:
        package_name = __package__
        specs: list[ToolSpec] = []
        load_errors: dict[str, str] = {}
        for module_info in pkgutil.iter_modules([str(self.package_dir)]):
            module_name = module_info.name
            if module_name.startswith("_") or module_name in INTERNAL_MODULES:
                continue
            full_name = f"{package_name}.{module_name}"
            try:
                if full_name in sys.modules:
                    module = importlib.reload(sys.modules[full_name])
                else:
                    module = importlib.import_module(full_name)
                spec = self._spec_from_module(module_name, module)
                specs.append(spec)
            except Exception as exc:
                load_errors[module_name] = str(exc)

        ordered = sorted(specs, key=lambda spec: (spec.order, spec.name))
        self.load_errors = load_errors
        self._snapshot = self._current_snapshot()
        return {spec.name: spec for spec in ordered}

    def _spec_from_module(self, module_name: str, module: Any) -> ToolSpec:
        try:
            tool_name = str(module.TOOL_NAME)
            definition = module.TOOL_DEFINITION
            runner = module.run
        except AttributeError as exc:
            raise RuntimeError(
                f"Tool module '{module_name}' must define TOOL_NAME, TOOL_DEFINITION, and run(context, args)."
            ) from exc

        function_name = definition.get("function", {}).get("name")
        if definition.get("type") != "function" or function_name != tool_name:
            raise RuntimeError(f"Tool module '{module_name}' has a TOOL_DEFINITION name mismatch.")

        return ToolSpec(
            name=tool_name,
            definition=definition,
            runner=runner,
            requires_container=bool(getattr(module, "REQUIRES_CONTAINER", False)),
            order=int(getattr(module, "TOOL_ORDER", 1000)),
        )

    def _refresh_if_changed(self) -> None:
        if self._current_snapshot() != self._snapshot:
            self.tool_specs = self._load_tool_specs()

    def _current_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        snapshot: list[tuple[str, int, int]] = []
        for path in self.package_dir.glob("*.py"):
            module_name = path.stem
            if module_name.startswith("_") or module_name in INTERNAL_MODULES:
                continue
            stat = path.stat()
            snapshot.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(snapshot))
