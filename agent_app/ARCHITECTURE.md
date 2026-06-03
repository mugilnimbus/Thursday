# Thursday Architecture

The codebase is split by responsibility. Keep new code inside the component that owns the behavior.

## Frontend

- `frontend/`
- Static dashboard assets only: HTML, CSS, and browser JavaScript.
- Talks to the Python backend through `/api/*`.
- Does not import Python or know about tool internals.

## Backend

- `agent_app/backend/`
- HTTP server and API request/response handling.
- Reads request payloads, returns JSON/static files, and delegates work to runtime state.
- Does not run agent loops, parse tool calls, or call tools directly.

## Runtime

- `agent_app/runtime/`
- Long-lived app state: sessions, preferences, reminders, and background scheduler wiring.
- Owns lifecycle concerns and calls the orchestrator when a turn should run.
- Does not parse model tool calls or implement HTTP routes.

## Orchestration

- `agent_app/orchestration/`
- Agent control flow only: turn setup, LLM step loop, retry/stop/done transitions, and session event sequencing.
- `orchestrator.py` delegates specialized work to collaborators.
- `messages.py` normalizes user/model/image message payloads.
- `context_manager.py` owns token metrics, pruning, summaries, and write-file context compression.
- Permanent operating instructions and the always-active `tool_operations` package are added as user-role timeline messages.
- When the model calls `load_skill`, the orchestrator appends the returned skill text as a user-role instruction message.
- Does not decide which concrete tool to call.

## Skills

- `prompts/thursday.md`
- Stable core prompt: mission, environment constants, and the instruction-message contract.

- `agent_app/skills/`
- Discovers skill packages from `prompts/skills/<skill_name>/SKILL.md`.
- Parses required frontmatter metadata: `name` and `description`.
- Lists optional bundled resources under `references/`, `scripts/`, and `assets/`.
- Keeps skill discovery and validation out of orchestration and tools.

- `prompts/skills/`
- Package folders. Each package has a required `SKILL.md` and optional `references/`, `scripts/`, and `assets/`.
- Skill metadata is shown in the always-active skill catalog.
- Skill bodies are loaded through `load_skill` only when relevant.
- Bundled resources are loaded through `read_skill_resource` only when needed.
- `tool_operations` is always active. Other skills are chosen by the LLM from metadata and loaded through `load_skill`.

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
- Converts path-based top-level message `images` into transport image payloads immediately before sending to LM Studio.
- Does not know about sessions, orchestration, or tools beyond receiving tool definitions in a chat request.

## Storage And Logging

- `agent_app/storage/`
- Session persistence and uploaded image persistence.
- Image uploads are stored as files; sessions keep path/URL metadata rather than base64 image bodies.

- `agent_app/logging_store/`
- App logs and raw LM Studio request/response logs.

## Compatibility Shims

The following top-level modules intentionally re-export the new components for older imports:

- `agent_app/http_app.py`
- `agent_app/orchestrator.py`
- `agent_app/state.py`
- `agent_app/session_store.py`
- `agent_app/llm_client.py`
- `agent_app/sqlite_logging.py`
