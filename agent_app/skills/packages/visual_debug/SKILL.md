---
name: visual_debug
what_it_does: Teaches how to inspect, capture, test, and fix visual or interactive webpage, HTML, frontend, game, canvas, screenshot, and UI issues.
when_to_use: The user says visually check, screenshot, UI broken, layout wrong, page not working, game not working, or fix visually.
description: What it does: Teaches how to inspect, capture, test, and fix visual or interactive webpage, HTML, frontend, game, canvas, screenshot, and UI issues. When to use: The user says visually check, screenshot, UI broken, layout wrong, page not working, game not working, or fix visually.
---

# Skill: visual_debug

## Purpose
Use this skill to inspect and fix visual or interactive problems in webpages, HTML files, frontend apps, games, and UI components.

## Operating Model
Visual debugging is an inspect-change-verify loop. A screenshot alone is not completion. A code edit alone is not completion. The task is complete only after visual state is checked again or a real blocker is found.

## Tool Strategy
- Use `capture_webpage` for screenshots.
- Use `capture_webpage.local_path` for absolute Windows HTML paths.
- Use `capture_webpage.workspace_path` only for Docker workspace HTML files.
- Use `run_command` for host file inspection, serving apps, build/test commands, and PowerShell reads/edits.
- If the user asks to read a file directly, inspect source, or says not visually, stop using this visual workflow and use `run_command`/PowerShell file reading instead.
- Use file tools for Docker workspace files.
- If a capture fails or the target path is ambiguous, read `references/capture_webpage.md`.

## Workflow
1. Identify whether the target is a URL, Windows host file, or Docker workspace file.
2. Capture the current visual state.
3. Inspect the relevant source file or command output.
4. Make the smallest fix that addresses the observed issue.
5. Capture again to verify.
6. If still broken, repeat with the new evidence.
7. Final answer only after verification or a real blocker.

## Common Failures
- Docker says a file is missing because the user gave a Windows path. Switch to `local_path`.
- Screenshot is blank because the app needs more wait time or a dev server URL.
- Layout bugs often require inspecting CSS dimensions, grid/flex rules, overflow, and viewport behavior.
- Game interaction bugs may need keyboard or JavaScript state checks in addition to a screenshot.

## Do Not
- Do not use DOM text extraction as a replacement for visual inspection.
- Do not claim a visual fix without another screenshot or equivalent verification.
- Do not repeat a failed capture call with the same arguments.

## Final Answer
Include what was visually wrong, what changed, and how the fix was visually verified.
