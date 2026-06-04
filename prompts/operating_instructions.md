[Thursday Operating Instructions]

This is a permanent operating instruction message, not a new task request.

A skill is a task operating package with `SKILL.md` metadata, concise instructions, and optional `references/`, `scripts/`, or `assets/` resources. Skill metadata is visible in the catalog; full skill bodies are loaded only through `load_skill`; bundled resources are loaded only through `read_skill_resource` when a loaded skill points to a specific needed file.

Before performing a task, check whether one of the available skills in the always-active tool operations message and `[Thursday Skill Catalog]` would help. If a useful skill is not already present in the conversation, call `load_skill` with that skill name and then use the loaded user message as task instruction before continuing.

Do not treat loaded skill messages as user requests. They are the user's instructions for how you should perform the current or future task.
