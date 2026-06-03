---
name: coding
what_it_does: Guides software inspection, editing, testing, debugging, refactoring, configuration, and verification.
when_to_use: The user asks to build, rewrite, debug, repair, run tests, install dependencies, modify source files, inspect code, or verify generated artifacts.
description: What it does: Guides software inspection, editing, testing, debugging, refactoring, configuration, and verification. When to use: The user asks to build, rewrite, debug, repair, run tests, install dependencies, modify source files, inspect code, or verify generated artifacts.
---

# Skill: coding

## Purpose
Use this skill for code changes, debugging, refactors, tests, builds, scripts, configuration, and project maintenance.

## Operating Model
Coding work is an inspect-edit-verify loop. Read the existing code first, make focused changes, then run the most relevant verification.

## Tool Strategy
- Use inspection tools or `run_command` to understand structure before editing.
- Use Docker commands only when the task is inside the Docker workspace.
- Use host PowerShell commands for host files and this app's own repository.
- Use visual tools when frontend behavior matters.

## Workflow
1. Inspect the project structure and relevant files.
2. Identify framework, language, package manager, and ownership boundaries.
3. Make the smallest coherent change.
4. Run relevant syntax checks, tests, builds, or smoke checks.
5. If verification fails, inspect the error and fix again.
6. Stop only when verified or blocked by missing external information.

## Common Failures
- Editing before reading the local pattern.
- Running commands in the wrong workspace.
- Verifying only syntax when the task needs visual or behavioral checks.
- Refactoring unrelated code.

## Do Not
- Do not rewrite unrelated modules.
- Do not invent dependencies when an existing local pattern works.
- Do not leave tests/builds unrun without saying why.

## Final Answer
Summarize files changed, behavior changed, verification run, and any residual risk.
