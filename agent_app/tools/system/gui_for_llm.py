from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ._screen_control import (
    capture_region,
    display_by_id,
    execute_key_sequence,
    execute_mouse,
    execute_text,
    get_active_region,
    load_displays,
    region_from_target,
    region_point_to_absolute,
    set_active_region,
    wait_seconds,
)


TOOL_NAME = "gui_for_llm"
TOOL_ORDER = 82
REQUIRES_CONTAINER = False

INPUT_ACTIONS = {"click", "double_click", "move", "scroll", "type", "key", "hotkey"}

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Operate only inside the user-selected dashboard GUI region: capture screenshots for VLM inspection, "
            "then optionally execute mouse/keyboard actions and verify with another screenshot. The model must use "
            "the active region set by the user; do not choose a new region unless the user asks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Operation to perform.",
                    "enum": [
                        "list_displays",
                        "get_active_region",
                        "set_active_region",
                        "screenshot",
                        "click",
                        "double_click",
                        "move",
                        "scroll",
                        "type",
                        "key",
                        "hotkey",
                        "wait",
                    ],
                },
                "target": {
                    "type": "object",
                    "description": "Optional target. Use only when the user explicitly asks to change region.",
                    "properties": {
                        "display": {"type": "integer", "description": "Display id from list_displays."},
                        "region": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                                "width": {"type": "integer"},
                                "height": {"type": "integer"},
                            },
                        },
                    },
                },
                "use_active_region": {
                    "type": "boolean",
                    "description": "Use the dashboard-selected active region. Default true.",
                    "default": True,
                },
                "coordinate_space": {
                    "type": "string",
                    "description": "Coordinate space for x/y input actions.",
                    "enum": ["llm_image", "active_region", "region", "display", "screen"],
                    "default": "llm_image",
                },
                "x": {"type": "integer", "description": "X coordinate for mouse actions."},
                "y": {"type": "integer", "description": "Y coordinate for mouse actions."},
                "button": {
                    "type": "string",
                    "description": "Mouse button for click actions.",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                },
                "scroll_delta": {
                    "type": "integer",
                    "description": "Mouse wheel delta. Positive scrolls up, negative scrolls down.",
                    "default": -500,
                },
                "text": {"type": "string", "description": "Text to type for action=type."},
                "keys": {
                    "type": "array",
                    "description": "Keys for key/hotkey, e.g. ['ctrl','l'] or ['enter'].",
                    "items": {"type": "string"},
                    "maxItems": 4,
                },
                "seconds": {
                    "type": "number",
                    "description": "Seconds to wait for action=wait. Maximum 10.",
                    "default": 1,
                },
                "allow_input": {
                    "type": "boolean",
                    "description": "Required true for mouse/keyboard actions. Omit for screenshot/list actions.",
                    "default": False,
                },
                "verify_after": {
                    "type": "boolean",
                    "description": "Capture a screenshot after an input action. Default true.",
                    "default": True,
                },
            },
            "required": ["action"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip().lower()
    if not action:
        return {"ok": False, "error": "Provide action."}

    if action == "list_displays":
        return list_displays_result(context)

    if action == "get_active_region":
        return active_region_result(context)

    if action == "set_active_region":
        region = set_active_region(context, args)
        display = display_by_id(region.display)
        return {
            "ok": True,
            "message": "Active GUI region saved.",
            "active_region": region.as_dict(),
            "display": display,
            "coordinate_rule": "Future active_region coordinates are relative to the top-left of this region.",
        }

    if action == "screenshot":
        return screenshot_result(context, args)

    if action == "wait":
        waited = wait_seconds(args.get("seconds", 1))
        return {"ok": True, "message": f"Waited {waited:g} seconds.", "seconds": waited}

    if action in INPUT_ACTIONS:
        if not bool(args.get("allow_input")):
            return {
                "ok": False,
                "error": "Mouse and keyboard actions require allow_input=true.",
                "recovery_hint": "Inspect the latest screenshot, then repeat the action with allow_input=true when the target is clear.",
            }
        return input_action_result(context, action, args)

    return {"ok": False, "error": f"Unknown gui_for_llm action: {action}"}


def list_displays_result(context: ToolContext) -> dict[str, Any]:
    active = get_active_region(context)
    return {
        "ok": True,
        "displays": load_displays(),
        "active_region": active.as_dict() if active else None,
        "coordinate_spaces": {
            "screen": "Absolute virtual-desktop coordinates.",
            "display": "Coordinates relative to selected display.",
            "active_region": "Coordinates relative to the saved dashboard-selected region screenshot.",
            "llm_image": "Coordinates relative to the attached screenshot image returned to the model. Use this for visual clicks.",
        },
    }


def active_region_result(context: ToolContext) -> dict[str, Any]:
    active = get_active_region(context)
    if not active:
        return {
            "ok": True,
            "active_region": None,
            "message": "No active region is saved. The user should set one in the dashboard GUI controls.",
        }
    return {"ok": True, "active_region": active.as_dict(), "display": display_by_id(active.display)}


def screenshot_result(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    region = region_from_target(context, args)
    captured = capture_region(context, region)
    return {
        "ok": True,
        "message": "Screenshot captured and attached for visual inspection.",
        **captured,
    }


def input_action_result(context: ToolContext, action: str, args: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"action": action}

    if action in {"click", "double_click", "move"}:
        require_xy(args)
        point = region_point_to_absolute(context, args)
        output.update(execute_mouse(action, point["x"], point["y"], str(args.get("button") or "left")))
        output["coordinate_space"] = str(args.get("coordinate_space") or "active_region")

    elif action == "scroll":
        point_args = dict(args)
        if point_args.get("x") is None or point_args.get("y") is None:
            region = region_from_target(context, args)
            point_args["x"] = region.width // 2
            point_args["y"] = region.height // 2
            point_args["coordinate_space"] = "active_region"
        point = region_point_to_absolute(context, point_args)
        delta = int(args.get("scroll_delta", -500) or -500)
        output.update(execute_mouse("scroll", point["x"], point["y"], scroll_delta=delta))
        output["scroll_delta"] = delta

    elif action == "type":
        text = str(args.get("text") or "")
        if not text:
            return {"ok": False, "error": "Provide text for action=type."}
        output.update(execute_text(text))

    elif action in {"key", "hotkey"}:
        keys = args.get("keys")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            return {"ok": False, "error": "Provide keys as an array for key/hotkey actions."}
        output.update(execute_key_sequence([str(key) for key in keys]))

    if bool(args.get("verify_after", True)):
        region = region_from_target(context, args)
        output["verification"] = capture_region(context, region)
        output["llm_images"] = output["verification"]["llm_images"]
        output["message"] = "Input action executed. Verification screenshot attached."
    else:
        output["message"] = "Input action executed without verification screenshot."

    return {"ok": True, **output}


def require_xy(args: dict[str, Any]) -> None:
    if args.get("x") is None or args.get("y") is None:
        raise ValueError("Mouse actions require x and y coordinates.")
