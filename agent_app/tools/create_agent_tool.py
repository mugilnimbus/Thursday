from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from .context import ToolContext

TOOL_NAME = "create_agent_tool"
TOOL_ORDER = 130
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Create or update a Thursday agent tool module in the host app's agent_app/tools folder. "
            "Use this when the user asks Thursday to add a new capability to itself. "
            "Either provide complete Python source_code for the tool, or provide tool_name, description, "
            "parameters_schema, requires_container, and order to create a scaffold. "
            "The new tool is discovered automatically on the next tool-list refresh or model turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Lowercase snake_case tool name. This becomes agent_app/tools/<tool_name>.py.",
                },
                "description": {
                    "type": "string",
                    "description": "Clear model-facing description for the tool. Required when source_code is empty.",
                    "default": "",
                },
                "parameters_schema": {
                    "type": "object",
                    "description": "JSON schema object for tool arguments. Required when source_code is empty.",
                    "default": {"type": "object", "properties": {}},
                },
                "requires_container": {
                    "type": "boolean",
                    "description": "Set true if the tool must run only when the Docker workspace container is running.",
                    "default": False,
                },
                "order": {
                    "type": "integer",
                    "description": "Display/order number in the tool list.",
                    "default": 1000,
                },
                "source_code": {
                    "type": "string",
                    "description": "Complete Python source for the tool module. If provided, it is validated and written as-is.",
                    "default": "",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Allow replacing an existing tool module with the same name.",
                    "default": False,
                },
            },
            "required": ["tool_name"],
        },
    },
}

TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
RESERVED_MODULES = {"__init__", "base", "context", "parsers", "registry"}
REQUIRED_NAMES = {"TOOL_NAME", "TOOL_DEFINITION", "run"}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(args["tool_name"]).strip()
    validation_errors = validate_tool_name(tool_name)
    if validation_errors:
        return {"ok": False, "error": "; ".join(validation_errors), "validation_errors": validation_errors}

    tools_dir = Path(__file__).parent
    tool_path = tools_dir / f"{tool_name}.py"
    if tool_path.exists() and not bool(args.get("overwrite", False)):
        return {
            "ok": False,
            "error": f"Tool already exists: {tool_name}. Use overwrite=true to replace it.",
            "path": str(tool_path.resolve()),
        }

    source_code = str(args.get("source_code") or "")
    if not source_code.strip():
        description = str(args.get("description") or "").strip()
        if not description:
            return {"ok": False, "error": "description is required when source_code is empty."}
        parameters_schema = args.get("parameters_schema") or {"type": "object", "properties": {}}
        source_code = build_tool_scaffold(
            tool_name=tool_name,
            description=description,
            parameters_schema=parameters_schema,
            requires_container=bool(args.get("requires_container", False)),
            order=int(args.get("order", 1000)),
        )

    source_code = source_code.strip() + "\n"
    source_errors = validate_source(tool_name, source_code)
    if source_errors:
        return {"ok": False, "error": "; ".join(source_errors), "validation_errors": source_errors}

    tool_path.write_text(source_code, encoding="utf-8")
    return {
        "ok": True,
        "tool_name": tool_name,
        "path": str(tool_path.resolve()),
        "created_or_updated": "updated" if bool(args.get("overwrite", False)) else "created",
        "available_next_turn": True,
        "restart_required": False,
        "message": "Tool module written. The registry hot-reloads tool files, so it will be available on the next tool-list refresh or model turn.",
    }


def validate_tool_name(tool_name: str) -> list[str]:
    errors: list[str] = []
    if not TOOL_NAME_PATTERN.fullmatch(tool_name):
        errors.append("tool_name must be lowercase snake_case and start with a letter")
    if tool_name in RESERVED_MODULES:
        errors.append(f"tool_name is reserved: {tool_name}")
    return errors


def build_tool_scaffold(
    tool_name: str,
    description: str,
    parameters_schema: Any,
    requires_container: bool,
    order: int,
) -> str:
    schema = parameters_schema if isinstance(parameters_schema, dict) else {"type": "object", "properties": {}}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    definition = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description,
            "parameters": schema,
        },
    }
    return (
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        "from .context import ToolContext\n\n"
        f"TOOL_NAME = {tool_name!r}\n"
        f"TOOL_ORDER = {int(order)}\n"
        f"REQUIRES_CONTAINER = {bool(requires_container)!r}\n"
        f"TOOL_DEFINITION = {json.dumps(definition, indent=4, ensure_ascii=False)}\n\n\n"
        "def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:\n"
        "    return {\n"
        "        \"ok\": False,\n"
        "        \"error\": \"Tool scaffold created. Implement this run() function before using the tool.\",\n"
        "        \"received_args\": args,\n"
        "    }\n"
    )


def validate_source(expected_tool_name: str, source_code: str) -> list[str]:
    errors: list[str] = []
    try:
        tree = ast.parse(source_code)
        compile(source_code, f"{expected_tool_name}.py", "exec")
    except SyntaxError as exc:
        return [f"Python syntax error on line {exc.lineno}: {exc.msg}"]

    defined_names = set()
    literal_tool_name = ""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined_names.add(node.name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_names.add(target.id)
                    if target.id == "TOOL_NAME" and isinstance(node.value, ast.Constant):
                        literal_tool_name = str(node.value.value)

    missing = sorted(REQUIRED_NAMES - defined_names)
    if missing:
        errors.append(f"Tool source is missing required names: {', '.join(missing)}")
    if literal_tool_name and literal_tool_name != expected_tool_name:
        errors.append(f"TOOL_NAME must be {expected_tool_name!r}, got {literal_tool_name!r}")
    return errors
