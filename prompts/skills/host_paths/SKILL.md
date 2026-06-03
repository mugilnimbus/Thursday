---
name: host_paths
what_it_does: Teaches how to distinguish Windows host paths from Docker workspace paths and recover from host-to-container confusion.
when_to_use: The user provides a C:\ path, asks about files outside Docker, mentions /workspace confusion, or a tool fails because a host file was treated as a Docker workspace file.
description: What it does: Teaches how to distinguish Windows host paths from Docker workspace paths and recover from host-to-container confusion. When to use: The user provides a C:\ path, asks about files outside Docker, mentions /workspace confusion, or a tool fails because a host file was treated as a Docker workspace file.
---

# Skill: host_paths

## Purpose
Use this skill to handle Windows host paths and avoid confusing host files with Docker workspace files.

## Operating Model
Thursday runs on a Windows host and also has a Docker workspace. These are different filesystems. Tools do not automatically decide where a path belongs.

## Tool Strategy
- Use `run_command` with PowerShell for Windows host file reads, tests, path checks, copies, and edits.
- Use `run_command` with `Get-Content -Raw -LiteralPath 'C:\...'` when the user asks to read, inspect source, describe file contents, or says not to inspect visually.
- Use `capture_webpage.local_path` for absolute Windows HTML files only when the user asks for a visual check, screenshot, rendered page inspection, or UI debugging.
- Use Docker commands or Docker workspace file tools only for files that actually live inside the Docker workspace.

## Workflow
1. Classify the path as Windows host, Docker workspace, URL, or unknown.
2. For `C:\...` paths, stay on the Windows host.
3. For direct file reads, call `run_command` with PowerShell `Get-Content -Raw -LiteralPath`.
4. For visual checks of host HTML, call `capture_webpage` with `local_path`.
5. For host source edits, use PowerShell commands through `run_command`.
6. Only copy files into Docker if the user explicitly asks or the task requires a Docker build/test.

## Common Failures
- Passing a Windows path to `workspace_path`.
- Trying to inspect `/workspace/...` when the file exists only under `C:\Users\...`.
- Copying host files into Docker just to inspect them visually.

## Do Not
- Do not pass absolute Windows paths as Docker `workspace_path` values.
- Do not assume a host folder is mounted inside Docker.
- Do not use `capture_webpage` when the user explicitly asks to read the file directly, inspect source, or avoid visual inspection.
- Do not copy host files into Docker unless there is a clear reason.

## Final Answer
State whether the file was handled as a host path or Docker path when that distinction matters.
