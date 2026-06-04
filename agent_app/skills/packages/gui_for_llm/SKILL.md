---
name: gui_for_llm
what_it_does: Teaches how to use the dashboard-controlled GUI region tool for visual screen inspection, mouse actions, keyboard actions, and verification screenshots.
when_to_use: The user asks the agent to operate a visible app, inspect the desktop by screenshot, click/type/scroll by sight, or use the see-screen decide-action verify-result loop.
description: What it does: Teaches how to use the dashboard-controlled GUI region tool for visual screen inspection, mouse actions, keyboard actions, and verification screenshots. When to use: The user asks the agent to operate a visible app, inspect the desktop by screenshot, click/type/scroll by sight, or use the see-screen decide-action verify-result loop.
---

# Skill: gui_for_llm

## Purpose
Use this skill when the task requires visual GUI operation:

```text
see screen -> understand state -> decide next action -> execute mouse/keyboard -> verify result -> repeat
```

The user controls the screen and region from the dashboard. The model should not choose a new monitor or region unless the user explicitly asks.

## Main Tool
Use `gui_for_llm`.

All calls must pass arguments inside `input`.

## Dashboard Region Rule
- The dashboard has a GUI For LLM control below the Context meter in the right tab.
- The user selects the display and region there.
- The tool uses that saved active region by default.
- The model should use `action: "screenshot"` without a `target` for normal visual inspection.
- Only call `set_active_region` if the user explicitly asks the agent to change the GUI region.

## Safe Workflow
1. Call `gui_for_llm` with `action: "get_active_region"` if you need to confirm the saved region.
2. Call `gui_for_llm` with `action: "screenshot"` and inspect the attached image.
3. Treat the screenshot attachment as visual input, not as a file path to ask the user to open.
4. Read the screenshot result's `coordinate_hint`.
5. Decide the smallest safe action.
6. For visual mouse actions, use coordinates from the attached screenshot and set `coordinate_space: "llm_image"`.
7. For every mouse/keyboard action, pass `allow_input: true`.
8. Use `verify_after: true` unless the user asked for no verification.
9. Inspect the verification screenshot before continuing.
10. Repeat until the task is complete.

Important: If an action is `click`, `double_click`, `move`, `scroll`, `type`, `key`, or `hotkey`, include `allow_input: true`. Without it, the tool will reject the action.

## Common Calls

Get the user-selected region:

```json
{ "input": { "action": "get_active_region" } }
```

Capture the user-selected region:

```json
{ "input": { "action": "screenshot" } }
```

Click inside the screenshot:

```json
{
  "input": {
    "action": "click",
    "x": 420,
    "y": 260,
    "coordinate_space": "llm_image",
    "allow_input": true,
    "verify_after": true
  }
}
```

Type text into the focused field:

```json
{
  "input": {
    "action": "type",
    "text": "example text",
    "allow_input": true,
    "verify_after": true
  }
}
```

Use a hotkey:

```json
{
  "input": {
    "action": "hotkey",
    "keys": ["ctrl", "l"],
    "allow_input": true,
    "verify_after": true
  }
}
```

Scroll in the active region:

```json
{
  "input": {
    "action": "scroll",
    "scroll_delta": -600,
    "allow_input": true,
    "verify_after": true
  }
}
```

## Safety Rules
- Always inspect a screenshot before mouse/keyboard actions.
- When a screenshot is attached, describe or reason from the pixels directly. Do not say you only have paths or cannot inspect screenshots.
- Do not click if the target is unclear.
- Do not invent coordinates from memory; use the latest screenshot.
- Use `llm_image` coordinates for clicks chosen from the attached screenshot.
- Use `active_region`, `display`, or `screen` coordinates only if the user or a tool result gives exact coordinates in that coordinate space.
- Do not type secrets, passwords, tokens, private messages, or payment details unless the user explicitly provides them for this exact task.
- Avoid destructive hotkeys and system shortcuts.
- If the screen changes unexpectedly, stop and inspect again.

## Final Answer
Mention what was done and what was visually verified. If the GUI could not be safely controlled, explain the blocker and the latest verified screen state.
