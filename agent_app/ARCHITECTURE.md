# Thursday Architecture

The codebase is split by responsibility. Keep new code inside the component that owns the behavior.

## Frontend

- `frontend/`
- Static dashboard assets only: HTML, CSS, and browser JavaScript.
- Talks to the Python server through `/api/*`.
- Does not import Python or know about tool internals.

## Server

- `agent_app/server/`
- HTTP server, request/response helpers, route handler, and static file serving.
- `http_server.py` only constructs the `ThreadingHTTPServer`.
- `handler.py` owns route control and delegates work to runtime state.
- `http_io.py` owns JSON and multipart parsing.
- `static_files.py` owns static frontend serving.
- Does not run agent loops, parse tool calls, or call tools directly.

## Runtime

- `agent_app/runtime/`
- Long-lived app state: sessions, preferences, reminders, and background scheduler wiring.
- `config.py`, `models.py`, `preferences.py`, `workspace.py`, `maintenance.py`, and `model_context.py` isolate app lifecycle concerns.
- Reminder data, schedule math, and SQLite persistence are split across `reminder_models.py`, `reminder_schedule.py`, and `storage/reminder_store.py`.
- Owns lifecycle concerns and calls the orchestrator when a turn should run.
- Does not parse model tool calls or implement HTTP routes.

## Orchestration

- `agent_app/orchestration/`
- Agent control flow only: turn setup, LLM step loop, retry/stop/done transitions, and session event sequencing.
- `orchestrator.py` delegates specialized work to collaborators and keeps the turn loop readable.
- `messages.py` normalizes user/model/image message payloads.
- `context_manager.py` owns token metrics, pruning, summaries, and write-file context compression.
- `instructions.py` owns permanent instruction messages and loaded-skill insertion.
- `response_chain.py` owns Responses API cache-chain state.
- `conversation_state.py` owns message append/backup/image-consumption helpers.
- Permanent operating instructions and the always-active tool operations prompt are added as user-role timeline messages.
- When the model calls `load_skill`, the orchestrator appends the returned skill text as a user-role instruction message.
- Does not decide which concrete tool to call.

## Skills

- `prompts/thursday.md`
- Stable core prompt: mission, environment constants, and the instruction-message contract.

- `prompts/operating_instructions.md`
- Permanent user-role operating instruction message.

- `prompts/tool_operations.md`
- Always-active user-role tool and skill operating manual.

- `agent_app/skills/`
- Discovers skill packages from `agent_app/skills/packages/<skill_name>/SKILL.md`.
- Parses required frontmatter metadata: `name` and `description`.
- Lists optional bundled resources under `references/`, `scripts/`, and `assets/`.
- Keeps skill discovery and validation out of orchestration and tools.

- `agent_app/skills/packages/`
- Package folders. Each package has a required `SKILL.md` and optional `references/`, `scripts/`, and `assets/`.
- Skill metadata is shown in the always-visible skill catalog.
- Skill bodies are loaded through `load_skill` only when relevant.
- Bundled resources are loaded through `read_skill_resource` only when needed.
- Skills are chosen by the LLM from metadata and loaded through `load_skill`.

## Tool Parser And Dispatcher

- `agent_app/tools/dispatcher.py`
- Parses raw model tool-call objects.
- Extracts tool call id, tool name, and decoded arguments.
- Invokes the tool registry with the parsed call.
- This is the only layer that maps a model tool call to a registered tool invocation.

## Tool Results

- `agent_app/tools/results.py`
- Shapes raw tool output into LLM observations and session-facing metadata.
- Handles tool images, modified-file markers, error text, fallback summaries, and write-file compression payload detection.
- Keeps tool-specific result formatting out of the orchestrator.

## Tools

- `agent_app/tools/registry.py`
- Auto-discovers tool modules under `agent_app/tools/workspace/` and `agent_app/tools/system/`.
- Enforces enabled-tool and Docker-container checks before running a tool.

- `agent_app/tools/workspace/`
- Tools that inspect or mutate the Docker workspace.

- `agent_app/tools/system/`
- Tools for system-level actions such as reminders, web search, or creating new tool modules.

- `agent_app/tools/context.py`
- Shared tool runtime helpers such as Docker execution, workspace path safety, browser helpers, and reminder context.

## LLM

- `agent_app/llm/`
- LM Studio/OpenAI-compatible chat client and endpoint status checks.
- `lmstudio_client.py` coordinates requests and endpoint/model status checks.
- `message_transport.py` converts path-based top-level message `images` into transport image payloads immediately before sending to LM Studio.
- `response_parsing.py` normalizes Responses API and Chat Completions responses into one internal shape.
- Does not know about sessions, orchestration, or tools beyond receiving tool definitions in a chat request.

## Storage And Logging

- `agent_app/storage/`
- Session persistence, uploaded image persistence, and reminder persistence.
- Image uploads are stored as files; sessions keep path/URL metadata rather than base64 image bodies.

- `agent_app/logging/`
- App logs and raw LM Studio request/response logs.
- `sqlite_handler.py` owns the Python logging handler.
- `raw_lmstudio_logs.py` owns LM Studio request/response records and legacy JSONL import.
- `app_logs.py` owns app log queries and clearing log tables.
- `sqlite_logs.py` is only the public facade for existing logging imports.

## Utilities

- `agent_app/utils/`
- Small generic helpers split by concern: JSON argument parsing, path safety, text clamping, token estimation, and UTC timestamps.

## Root Package

The root `agent_app/` package contains only `__init__.py`, `ARCHITECTURE.md`, and component folders. Do not add implementation modules at the package root.
