from __future__ import annotations

import base64
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..context import ToolContext


STATE_FILE_NAME = "active_region.json"
LLM_IMAGE_MAX_SIDE = 1600


@dataclass(frozen=True)
class Region:
    display: int
    x: int
    y: int
    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {
            "display": self.display,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


def screen_root(context: ToolContext) -> Path:
    root = context.config.visual_check_dir / "gui_for_llm"
    root.mkdir(parents=True, exist_ok=True)
    return root


def state_file(context: ToolContext) -> Path:
    return screen_root(context) / STATE_FILE_NAME


def run_powershell(script: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        text=True,
        capture_output=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def load_displays() -> list[dict[str, Any]]:
    script = r"""
Add-Type @'
using System.Runtime.InteropServices;
public class ThursdayDpi {
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
}
'@
try { [ThursdayDpi]::SetProcessDpiAwareness(2) | Out-Null } catch {}
Add-Type -AssemblyName System.Windows.Forms
$index = 0
[System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
  [pscustomobject]@{
    id = $index
    device_name = $_.DeviceName
    primary = $_.Primary
    x = $_.Bounds.X
    y = $_.Bounds.Y
    width = $_.Bounds.Width
    height = $_.Bounds.Height
    working_x = $_.WorkingArea.X
    working_y = $_.WorkingArea.Y
    working_width = $_.WorkingArea.Width
    working_height = $_.WorkingArea.Height
  }
  $index += 1
} | ConvertTo-Json -Compress
"""
    result = run_powershell(script)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to list displays.")
    raw = result.stdout.strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, dict):
        return [payload]
    return payload if isinstance(payload, list) else []


def display_by_id(display_id: int) -> dict[str, Any]:
    displays = load_displays()
    for display in displays:
        if int(display.get("id", -1)) == int(display_id):
            return display
    raise ValueError(f"Unknown display id {display_id}. Call list_displays first.")


def region_from_target(context: ToolContext, args: dict[str, Any]) -> Region:
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    if bool(args.get("use_active_region", True)):
        active = get_active_region(context)
        if active:
            return active

    display_id = int(target.get("display", args.get("display", 0)) or 0)
    display = display_by_id(display_id)
    region_payload = target.get("region") if isinstance(target.get("region"), dict) else args.get("region")
    if isinstance(region_payload, dict) and region_payload:
        x = bounded_int(region_payload.get("x"), 0, 0, int(display["width"]) - 1, "region.x")
        y = bounded_int(region_payload.get("y"), 0, 0, int(display["height"]) - 1, "region.y")
        width = bounded_int(region_payload.get("width"), int(display["width"]) - x, 20, int(display["width"]) - x, "region.width")
        height = bounded_int(region_payload.get("height"), int(display["height"]) - y, 20, int(display["height"]) - y, "region.height")
        return Region(display=display_id, x=x, y=y, width=width, height=height)
    return Region(display=display_id, x=0, y=0, width=int(display["width"]), height=int(display["height"]))


def set_active_region(context: ToolContext, args: dict[str, Any]) -> Region:
    region = region_from_target(context, {**args, "use_active_region": False})
    payload = {
        "active_region": region.as_dict(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    state_file(context).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return region


def get_active_region(context: ToolContext) -> Region | None:
    path = state_file(context)
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    region = payload.get("active_region") if isinstance(payload, dict) else None
    if not isinstance(region, dict):
        return None
    return Region(
        display=int(region.get("display", 0)),
        x=int(region.get("x", 0)),
        y=int(region.get("y", 0)),
        width=int(region.get("width", 0)),
        height=int(region.get("height", 0)),
    )


def capture_region(context: ToolContext, region: Region) -> dict[str, Any]:
    display = display_by_id(region.display)
    absolute_x = int(display["x"]) + region.x
    absolute_y = int(display["y"]) + region.y
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{region.display}_{region.width}x{region.height}"
    output_dir = screen_root(context) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / "screenshot.png"
    llm_screenshot_path = output_dir / "llm_screenshot.png"
    ps_path = str(screenshot_path).replace("'", "''")
    ps_llm_path = str(llm_screenshot_path).replace("'", "''")
    script = f"""
Add-Type @'
using System.Runtime.InteropServices;
public class ThursdayDpi {{
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
}}
'@
try {{ [ThursdayDpi]::SetProcessDpiAwareness(2) | Out-Null }} catch {{}}
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap({region.width}, {region.height})
$graphics = [System.Drawing.Graphics]::FromImage($bmp)
try {{
  $graphics.CopyFromScreen({absolute_x}, {absolute_y}, 0, 0, $bmp.Size)
  $bmp.Save('{ps_path}', [System.Drawing.Imaging.ImageFormat]::Png)
  $maxSide = {LLM_IMAGE_MAX_SIDE}
  $scale = [Math]::Min(1.0, $maxSide / [double][Math]::Max($bmp.Width, $bmp.Height))
  if ($scale -lt 1.0) {{
    $thumbWidth = [Math]::Max(1, [int][Math]::Round($bmp.Width * $scale))
    $thumbHeight = [Math]::Max(1, [int][Math]::Round($bmp.Height * $scale))
    $thumb = New-Object System.Drawing.Bitmap($thumbWidth, $thumbHeight)
    $thumbGraphics = [System.Drawing.Graphics]::FromImage($thumb)
    try {{
      $thumbGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
      $thumbGraphics.DrawImage($bmp, 0, 0, $thumbWidth, $thumbHeight)
      $thumb.Save('{ps_llm_path}', [System.Drawing.Imaging.ImageFormat]::Png)
    }} finally {{
      $thumbGraphics.Dispose()
      $thumb.Dispose()
    }}
  }} else {{
    Copy-Item -LiteralPath '{ps_path}' -Destination '{ps_llm_path}' -Force
  }}
}} finally {{
  $graphics.Dispose()
  $bmp.Dispose()
}}
[pscustomobject]@{{ path = '{ps_path}'; llm_path = '{ps_llm_path}'; width = {region.width}; height = {region.height} }} | ConvertTo-Json -Compress
"""
    result = run_powershell(script, timeout=30)
    if result.returncode != 0 or not screenshot_path.exists() or not llm_screenshot_path.exists():
        raise RuntimeError(result.stderr.strip() or "Screenshot capture failed.")
    llm_width, llm_height, llm_scale = llm_image_size_for_region(region)
    return {
        "screenshot_path": str(screenshot_path.resolve()),
        "llm_screenshot_path": str(llm_screenshot_path.resolve()),
        "display": display,
        "region": region.as_dict(),
        "absolute_region": {
            "x": absolute_x,
            "y": absolute_y,
            "width": region.width,
            "height": region.height,
        },
        "coordinate_hint": {
            "default_for_visual_clicks": "llm_image",
            "llm_image_width": llm_width,
            "llm_image_height": llm_height,
            "llm_to_active_region_scale": round(1 / llm_scale, 6) if llm_scale else 1,
            "rule": "When choosing a point from the attached screenshot, call input actions with coordinate_space='llm_image'.",
        },
        "llm_images": [
            {
                "source": "gui_for_llm",
                "path": str(llm_screenshot_path.resolve()),
                "mime_type": "image/png",
            }
        ],
    }


def region_point_to_absolute(context: ToolContext, args: dict[str, Any]) -> dict[str, int]:
    x = int(args.get("x"))
    y = int(args.get("y"))
    coordinate_space = str(args.get("coordinate_space") or "active_region").strip().lower()

    if coordinate_space == "screen":
        return {"x": x, "y": y}

    region = region_from_target(context, args)
    display = display_by_id(region.display)
    if coordinate_space in {"active_region", "region"}:
        if x < 0 or y < 0 or x >= region.width or y >= region.height:
            raise ValueError(f"Point ({x}, {y}) is outside active region {region.width}x{region.height}.")
        return {"x": int(display["x"]) + region.x + x, "y": int(display["y"]) + region.y + y}

    if coordinate_space == "llm_image":
        llm_width, llm_height, llm_scale = llm_image_size_for_region(region)
        if x < 0 or y < 0 or x >= llm_width or y >= llm_height:
            raise ValueError(f"Point ({x}, {y}) is outside attached LLM image {llm_width}x{llm_height}.")
        active_x = min(region.width - 1, max(0, int(round(x / llm_scale))))
        active_y = min(region.height - 1, max(0, int(round(y / llm_scale))))
        return {"x": int(display["x"]) + region.x + active_x, "y": int(display["y"]) + region.y + active_y}

    if coordinate_space == "display":
        if x < 0 or y < 0 or x >= int(display["width"]) or y >= int(display["height"]):
            raise ValueError(f"Point ({x}, {y}) is outside display {region.display}.")
        return {"x": int(display["x"]) + x, "y": int(display["y"]) + y}

    raise ValueError("coordinate_space must be active_region, region, llm_image, display, or screen.")


def llm_image_size_for_region(region: Region) -> tuple[int, int, float]:
    scale = min(1.0, LLM_IMAGE_MAX_SIDE / max(region.width, region.height))
    width = max(1, int(round(region.width * scale)))
    height = max(1, int(round(region.height * scale)))
    return width, height, scale


def execute_mouse(action: str, x: int, y: int, button: str = "left", scroll_delta: int = 0) -> dict[str, Any]:
    button = button if button in {"left", "right", "middle"} else "left"
    button_flags = {
        "left": ("0x0002", "0x0004"),
        "right": ("0x0008", "0x0010"),
        "middle": ("0x0020", "0x0040"),
    }
    down, up = button_flags[button]
    if action == "scroll":
        script = f"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class NativeInput {{
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, int data, UIntPtr extraInfo);
}}
'@
try {{ [NativeInput]::SetProcessDpiAwareness(2) | Out-Null }} catch {{}}
[NativeInput]::SetCursorPos({x}, {y}) | Out-Null
[NativeInput]::mouse_event(0x0800, 0, 0, {scroll_delta}, [UIntPtr]::Zero)
"""
    elif action == "move":
        script = f"""
Add-Type @'
using System.Runtime.InteropServices;
public class NativeInput {{
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
}}
'@
try {{ [NativeInput]::SetProcessDpiAwareness(2) | Out-Null }} catch {{}}
[NativeInput]::SetCursorPos({x}, {y}) | Out-Null
"""
    else:
        second_click = "[NativeInput]::mouse_event({down}, 0, 0, 0, [UIntPtr]::Zero); [NativeInput]::mouse_event({up}, 0, 0, 0, [UIntPtr]::Zero)" if action == "double_click" else ""
        script = f"""
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class NativeInput {{
  [DllImport("shcore.dll")] public static extern int SetProcessDpiAwareness(int value);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, int data, UIntPtr extraInfo);
}}
'@
try {{ [NativeInput]::SetProcessDpiAwareness(2) | Out-Null }} catch {{}}
[NativeInput]::SetCursorPos({x}, {y}) | Out-Null
[NativeInput]::mouse_event({down}, 0, 0, 0, [UIntPtr]::Zero)
[NativeInput]::mouse_event({up}, 0, 0, 0, [UIntPtr]::Zero)
{second_click.format(down=down, up=up)}
"""
    result = run_powershell(script, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"{action} failed.")
    return {"absolute_x": x, "absolute_y": y, "button": button}


def execute_text(text: str) -> dict[str, Any]:
    encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
$oldClipboard = $null
$hadClipboard = $false
try {{
  $oldClipboard = Get-Clipboard -Raw -ErrorAction Stop
  $hadClipboard = $true
}} catch {{}}
$textBytes = [Convert]::FromBase64String('{encoded_text}')
$text = [System.Text.Encoding]::UTF8.GetString($textBytes)
Set-Clipboard -Value $text
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 150
if ($hadClipboard) {{
  Set-Clipboard -Value $oldClipboard
}}
"""
    result = run_powershell(script, timeout=max(10, min(60, len(text) // 20 + 10)))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Typing text failed.")
    return {"characters": len(text), "method": "clipboard_paste"}


def execute_key_sequence(keys: list[str]) -> dict[str, Any]:
    normalized = [normalize_key(key) for key in keys if str(key or "").strip()]
    if not normalized:
        raise ValueError("Provide at least one key.")
    combo = "".join(normalized)
    blocked = {"%{F4}", "^%{DELETE}", "^{ESC}", "{LWIN}", "{RWIN}"}
    if combo.upper() in blocked:
        raise ValueError(f"Blocked unsafe key sequence: {keys}")
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait('{combo}')
"""
    result = run_powershell(script, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Key sequence failed.")
    return {"keys": keys, "sendkeys": combo}


def normalize_key(key: str) -> str:
    value = str(key).strip().lower()
    modifiers = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}
    special = {
        "enter": "{ENTER}",
        "return": "{ENTER}",
        "tab": "{TAB}",
        "esc": "{ESC}",
        "escape": "{ESC}",
        "backspace": "{BACKSPACE}",
        "delete": "{DELETE}",
        "del": "{DELETE}",
        "space": " ",
        "up": "{UP}",
        "down": "{DOWN}",
        "left": "{LEFT}",
        "right": "{RIGHT}",
        "home": "{HOME}",
        "end": "{END}",
        "pageup": "{PGUP}",
        "pagedown": "{PGDN}",
    }
    if value in modifiers:
        return modifiers[value]
    if value in special:
        return special[value]
    if value.startswith("f") and value[1:].isdigit():
        return "{" + value.upper() + "}"
    if len(value) == 1:
        return sendkeys_escape(value)
    raise ValueError(f"Unsupported key: {key}")


def sendkeys_escape(text: str) -> str:
    replacements = {
        "{": "{{}",
        "}": "{}}",
        "+": "{+}",
        "^": "{^}",
        "%": "{%}",
        "~": "{~}",
        "(": "{(}",
        ")": "{)}",
        "[": "{[}",
        "]": "{]}",
    }
    return "".join(replacements.get(char, char) for char in text)


def bounded_int(value: Any, default: int, minimum: int, maximum: int, field: str) -> int:
    try:
        parsed = int(default if value is None or value == "" else value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer.")
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}.")
    return parsed


def wait_seconds(seconds: Any) -> float:
    try:
        parsed = float(seconds if seconds is not None else 1)
    except (TypeError, ValueError):
        raise ValueError("seconds must be numeric.")
    parsed = max(0.0, min(parsed, 10.0))
    time.sleep(parsed)
    return parsed
