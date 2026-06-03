# Thursday Project Tree

This is the intended clean component layout for the current agent codebase. Generated/runtime folders such as `.venv`, `__pycache__`, and most log artifacts are omitted.

```text
Thursday/
├─ frontend/
│  ├─ index.html                 # Dashboard shell
│  ├─ app.js                     # Browser-side state, API calls, UI events
│  └─ styles.css                 # Dashboard styling
│
├─ agent_app/
│  ├─ backend/
│  │  └─ http_server.py           # HTTP API routes and static frontend serving
│  │
│  ├─ runtime/
│  │  └─ app_state.py             # App wiring: sessions, preferences, reminders, workspace maintenance
│  │
│  ├─ orchestration/
│  │  ├─ orchestrator.py          # Agent turn loop and control flow only
│  │  ├─ messages.py              # Message normalization and image message shaping
│  │  └─ context_manager.py       # Context pruning, metrics, and content compression
│  │
│  ├─ llm/
│  │  └─ lmstudio_client.py        # LM Studio / OpenAI-compatible adapter
│  │
│  ├─ tools/
│  │  ├─ api.py                   # Unified tool input/output envelope
│  │  ├─ dispatcher.py            # Raw model tool-call parsing and dispatch handoff
│  │  ├─ registry.py              # Tool discovery, enable checks, invocation, catalog
│  │  ├─ results.py               # Tool observation shaping for the LLM and UI
│  │  ├─ parsers.py               # Shared parser helpers
│  │  ├─ context.py               # Tool execution context
│  │  ├─ system/
│  │  │  ├─ list_skills.py
│  │  │  ├─ load_skill.py
│  │  │  ├─ read_skill_resource.py
│  │  │  ├─ get_current_datetime_location.py
│  │  │  ├─ web_search.py
│  │  │  ├─ create_reminder.py
│  │  │  ├─ list_reminders.py
│  │  │  ├─ update_reminder.py
│  │  │  ├─ delete_reminder.py
│  │  │  └─ create_agent_tool.py
│  │  └─ workspace/
│  │     ├─ run_command.py         # General Windows command runner; Docker is explicit in command text
│  │     ├─ read_file.py           # Docker workspace file read
│  │     ├─ write_file.py          # Docker workspace file write
│  │     ├─ edit_file.py           # Docker workspace exact-text edit
│  │     └─ capture_webpage.py     # URL/local/workspace screenshot capture
│  │
│  ├─ skills/
│  │  └─ catalog.py                # SKILL.md discovery, metadata validation, resource reading
│  │
│  ├─ storage/
│  │  ├─ session_store.py          # SQLite-backed session persistence
│  │  └─ image_store.py            # Uploaded image storage and normalization
│  │
│  ├─ logging_store/
│  │  └─ sqlite_logs.py            # Structured log storage and cleanup
│  │
│  ├─ config.py                    # Environment-backed app configuration
│  ├─ models.py                    # Session and settings models
│  ├─ preferences.py               # Dashboard preferences and required tools
│  ├─ prompts.py                   # System prompt rendering
│  ├─ reminders.py                 # Reminder persistence and scheduling logic
│  ├─ workspace.py                 # Docker workspace status/reset helpers
│  └─ utils.py                     # Shared utility helpers
│
├─ prompts/
│  ├─ thursday.md                  # Base system prompt
│  ├─ skills/
│  │  ├─ tool_operations/
│  │  │  └─ SKILL.md               # Always-active tool and skill operating manual
│  │  ├─ coding/
│  │  │  └─ SKILL.md
│  │  ├─ current_info/
│  │  │  └─ SKILL.md
│  │  ├─ host_paths/
│  │  │  └─ SKILL.md
│  │  ├─ image_input/
│  │  │  └─ SKILL.md
│  │  ├─ reminders/
│  │  │  └─ SKILL.md
│  │  └─ visual_debug/
│  │     ├─ SKILL.md
│  │     └─ references/
│  │        └─ capture_webpage.md
│  └─ versions/                    # Prompt snapshots/versioned prompt data
│
├─ scripts/
│  ├─ server.py                    # Server entrypoint
│  ├─ thursday.ps1                 # Windows lifecycle helper
│  ├─ thursday.cmd                 # cmd wrapper
│  └─ reset-docker-workspace.ps1    # Docker workspace reset helper
│
├─ logs/
│  ├─ images/                      # Uploaded image artifacts
│  └─ visual_checks/               # Screenshot artifacts from visual tools
│
├─ docs/
│  ├─ components_diagram.svg
│  ├─ control_flow_diagram.svg
│  └─ project_tree.md
│
├─ .env                            # Local runtime config
├─ .env.example                    # Example runtime config
├─ pyproject.toml
├─ requirements.txt
├─ README.md
└─ main.py
```

## Legacy Compatibility Files

Some top-level `agent_app/*.py` files still exist beside the newer component folders, for example `agent_app/orchestrator.py`, `agent_app/http_app.py`, `agent_app/llm_client.py`, `agent_app/state.py`, `agent_app/session_store.py`, `agent_app/sqlite_logging.py`, and `agent_app/tools/base.py`.

Treat the folder-based modules as the clean architecture boundary. The top-level files should either be compatibility shims or candidates for removal after imports are fully migrated.

## Component Boundaries

- `frontend/` owns only the browser UI and HTTP calls.
- `backend/` owns request routing.
- `runtime/` wires app state and long-running services.
- `orchestration/` owns turn control flow only.
- `llm/` owns LM Studio API details.
- `tools/dispatcher.py` owns raw tool-call parsing.
- `tools/registry.py` owns tool lookup, enablement, and invocation.
- `tools/system/` and `tools/workspace/` own concrete tool behavior.
- `skills/catalog.py` owns skill discovery and metadata validation.
- `prompts/skills/*/SKILL.md` owns skill instructions and skill-selection metadata.
