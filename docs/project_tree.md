# Thursday Project Tree

This is the intended clean component layout for the current agent codebase. Generated/runtime folders such as `.venv`, `__pycache__`, and most log artifacts are omitted.

```text
Thursday/
├─ frontend/
│  ├─ index.html                  # Dashboard shell
│  ├─ app.js                      # Browser-side state, API calls, UI events
│  └─ styles.css                  # Dashboard styling
│
├─ agent_app/
│  ├─ server/
│  │  ├─ http_server.py            # ThreadingHTTPServer construction only
│  │  ├─ handler.py                # HTTP route control and runtime delegation
│  │  ├─ http_io.py                # JSON/multipart request and response helpers
│  │  └─ static_files.py            # Static frontend file serving
│  │
│  ├─ runtime/
│  │  ├─ app_state.py              # App wiring: sessions, preferences, reminders, workspace lifecycle
│  │  ├─ config.py                 # Environment-backed app configuration
│  │  ├─ models.py                 # Session, event, and agent settings models
│  │  ├─ preferences.py            # Dashboard preferences and required tools
│  │  ├─ workspace.py              # Docker workspace status/reset helpers
│  │  ├─ maintenance.py            # Log/artifact cleanup helpers
│  │  ├─ model_context.py          # Model context-window and token-budget helpers
│  │  ├─ reminder_models.py        # Reminder public data model
│  │  ├─ reminder_schedule.py      # Reminder schedule parsing and next-run math
│  │  └─ reminders.py              # Small compatibility export for reminder APIs
│  │
│  ├─ orchestration/
│  │  ├─ orchestrator.py           # Agent turn loop and control flow only
│  │  ├─ messages.py               # Message normalization and image message shaping
│  │  ├─ context_manager.py        # Context pruning, metrics, summaries, and compression
│  │  ├─ prompt_renderer.py        # Prompt asset rendering
│  │  ├─ conversation_state.py     # Message append, backup, clone, image-consumption helpers
│  │  ├─ instructions.py           # Permanent operating/tool instruction timeline messages
│  │  └─ response_chain.py         # Responses API previous_response_id cache bookkeeping
│  │
│  ├─ llm/
│  │  ├─ lmstudio_client.py         # LM Studio request orchestration and status checks
│  │  ├─ message_transport.py       # Message/image conversion for model transport
│  │  └─ response_parsing.py        # Responses API and chat completion response normalization
│  │
│  ├─ tools/
│  │  ├─ api.py                    # Unified tool input/output envelope
│  │  ├─ dispatcher.py             # Raw model tool-call parsing and dispatch handoff
│  │  ├─ registry.py               # Tool discovery, enable checks, invocation, catalog
│  │  ├─ results.py                # Tool observation shaping for the LLM and UI
│  │  ├─ parsers.py                # Shared parser helpers for tools
│  │  ├─ context.py                # Tool execution context and tool runtime helpers
│  │  ├─ system/                   # System-level callable tools
│  │  └─ workspace/                # Workspace/file/visual callable tools
│  │
│  ├─ skills/
│  │  ├─ catalog.py                 # SKILL.md discovery, metadata validation, resource reading
│  │  └─ packages/                  # Loadable skill packages
│  │
│  ├─ storage/
│  │  ├─ session_store.py           # SQLite-backed session persistence
│  │  ├─ image_store.py             # Uploaded image storage and normalization
│  │  └─ reminder_store.py          # SQLite-backed reminder persistence
│  │
│  ├─ logging/
│  │  ├─ sqlite_logs.py             # Public logging facade
│  │  ├─ sqlite_handler.py          # Python logging.Handler backed by SQLite
│  │  ├─ raw_lmstudio_logs.py       # Raw LM Studio request/response storage
│  │  └─ app_logs.py                # App log query and cleanup helpers
│  │
│  ├─ utils/
│  │  ├─ json.py                    # Generic JSON/argument helpers
│  │  ├─ paths.py                   # Generic path helpers
│  │  ├─ text.py                    # Generic text helpers
│  │  ├─ time.py                    # Generic time helpers
│  │  └─ tokens.py                  # Generic token estimation helpers
│  │
│  ├─ ARCHITECTURE.md               # Component boundary documentation
│  └─ __init__.py                   # Package marker only
│
├─ prompts/
│  ├─ thursday.md                   # Base system prompt
│  ├─ operating_instructions.md      # Permanent user-role operating instructions
│  ├─ tool_operations.md             # Always-active tool and skill operating manual
│  ├─ conversation_summary.md        # Context summarizer prompt
│  ├─ context_summary.md             # Stable compressed-context timeline prompt
│  ├─ file_write_summary.md          # Large write_file compression prompt
│  └─ versions/                      # Prompt snapshots/versioned prompt data
│
├─ scripts/
│  ├─ server.py                      # Server entrypoint
│  ├─ thursday.ps1                   # Windows lifecycle helper
│  ├─ thursday.cmd                   # cmd wrapper
│  └─ reset-docker-workspace.ps1      # Docker workspace reset helper
│
├─ logs/
│  ├─ context_summaries/             # Durable per-session context summaries
│  ├─ images/                        # Uploaded image artifacts
│  └─ visual_checks/                 # Screenshot artifacts from visual tools
│
├─ docs/
│  ├─ components_diagram.svg
│  ├─ control_flow_diagram.svg
│  └─ project_tree.md
│
├─ .env
├─ .env.example
├─ pyproject.toml
├─ README.md
└─ uv.lock
```

## Component Boundaries

- `frontend/` owns only the browser UI and HTTP calls.
- `server/` owns HTTP IO, request routing, and static serving.
- `runtime/` wires app state, preferences, Docker workspace lifecycle, reminders, and model context settings.
- `orchestration/` owns turn control flow, context compression, permanent instruction messages, and response-chain state.
- `llm/` owns LM Studio API details, transport message conversion, and response normalization.
- `tools/` owns callable tool contracts, parsing, dispatch, result formatting, and concrete tool behavior.
- `skills/catalog.py` owns skill discovery and metadata validation.
- `prompts/operating_instructions.md` and `prompts/tool_operations.md` own always-active instruction text.
- `agent_app/skills/packages/*/SKILL.md` owns loadable skill instructions and skill-selection metadata.
- `storage/` owns persistence for sessions, image files, and reminders.
- `logging/` owns structured log persistence and retrieval.
- `utils/` owns only small generic helpers with no agent policy.
