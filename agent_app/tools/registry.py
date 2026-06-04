from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path
from typing import Any

from ..runtime.config import AppConfig
from ..storage.reminder_store import ReminderStore
from .api import (
    elapsed_ms,
    error_response,
    normalize_legacy_result,
    output_envelope_schema,
    parse_input_envelope,
    started_timer,
    tool_definition_envelope,
)
from .base import ToolSpec
from .context import ToolContext, container_running, docker_not_running_error


INTERNAL_MODULES = {"api", "base", "context", "dispatcher", "parsers", "registry", "results"}
TOOL_PACKAGES = ("workspace", "system")


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
        return [tool_definition_envelope(spec.definition) for spec in self.tool_specs.values() if spec.name in enabled]

    def catalog(self, enabled_tools: list[str] | None = None) -> list[dict[str, Any]]:
        self._refresh_if_changed()
        enabled = set(self.names() if enabled_tools is None else enabled_tools)
        catalog: list[dict[str, Any]] = []
        for spec in self.tool_specs.values():
            if spec.name not in enabled:
                continue
            function = spec.definition.get("function", {})
            input_schema = function.get("parameters") if isinstance(function.get("parameters"), dict) else {"type": "object", "properties": {}}
            catalog.append(
                {
                    "name": spec.name,
                    "category": spec.category,
                    "description": str(function.get("description") or ""),
                    "input_schema": input_schema,
                    "call_schema": tool_definition_envelope(spec.definition)["function"]["parameters"],
                    "output_schema": output_envelope_schema(),
                    "requires_container": spec.requires_container,
                    "order": spec.order,
                    "module": spec.module_name,
                }
            )
        return catalog

    def invoke(self, name: str, args: dict[str, Any], enabled_tools: list[str] | None = None) -> dict[str, Any]:
        self._refresh_if_changed()
        input_args, _request_meta = parse_input_envelope(args or {})
        started = started_timer()
        if enabled_tools is not None and name not in enabled_tools:
            return error_response(name, f"Tool is disabled: {name}", input_args, elapsed_ms(started), error_type="DisabledTool")
        spec = self.tool_specs.get(name)
        if not spec:
            return error_response(name, f"Unknown tool: {name}", input_args, elapsed_ms(started), error_type="UnknownTool")
        if spec.requires_container and not container_running(self.config):
            docker_error = docker_not_running_error(self.config)
            return error_response(
                name,
                str(docker_error.get("error") or "Docker workspace is not running."),
                input_args,
                elapsed_ms(started),
                category=spec.category,
                requires_container=spec.requires_container,
                error_type="WorkspaceUnavailable",
            )
        try:
            result = spec.runner(self.context, input_args)
            return normalize_legacy_result(
                tool_name=name,
                result=result,
                input_args=input_args,
                duration_ms=elapsed_ms(started),
                category=spec.category,
                requires_container=spec.requires_container,
            )
        except Exception as exc:
            return error_response(
                name,
                str(exc),
                input_args,
                elapsed_ms(started),
                category=spec.category,
                requires_container=spec.requires_container,
                error_type=type(exc).__name__,
            )

    def _load_tool_specs(self) -> dict[str, ToolSpec]:
        specs: list[ToolSpec] = []
        load_errors: dict[str, str] = {}
        for module_info in self._iter_tool_modules():
            module_name = module_info.name
            if module_name.rsplit(".", 1)[-1].startswith("_"):
                continue
            full_name = f"{__package__}.{module_name}"
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

    def _iter_tool_modules(self) -> list[pkgutil.ModuleInfo]:
        modules: list[pkgutil.ModuleInfo] = []
        for package in TOOL_PACKAGES:
            tool_dir = self.package_dir / package
            if not tool_dir.exists():
                continue
            for module_info in pkgutil.iter_modules([str(tool_dir)], prefix=f"{package}."):
                if not module_info.ispkg:
                    modules.append(module_info)
        return modules

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
            module_name=module_name,
            category=module_name.split(".", 1)[0] if "." in module_name else "tool",
        )

    def _refresh_if_changed(self) -> None:
        if self._current_snapshot() != self._snapshot:
            self.tool_specs = self._load_tool_specs()

    def _current_snapshot(self) -> tuple[tuple[str, int, int], ...]:
        snapshot: list[tuple[str, int, int]] = []
        for path in self.package_dir.glob("**/*.py"):
            module_name = path.stem
            if module_name.startswith("_") or module_name in INTERNAL_MODULES:
                continue
            stat = path.stat()
            snapshot.append((path.name, stat.st_mtime_ns, stat.st_size))
        return tuple(sorted(snapshot))

