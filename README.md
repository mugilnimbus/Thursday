# Thursday

Thursday is a local software-building agent with a web dashboard for chat, tool control, monitoring, timelines, settings, and raw LM Studio logs.

The agent uses a local LM Studio server through OpenAI-compatible APIs, preferring `/v1/responses` with `previous_response_id` and falling back to chat completions when needed. Project tools execute only when the model calls the unified tool layer.

## What It Does

- Chats with a local LM Studio model.
- Lets the model call tools for files, commands, web search, and webpage inspection.
- Runs workspace tools inside the Docker container named `Thursday`.
- Shows session history, timeline events, tool permissions, logs, raw LLM input/output, and settings in the dashboard.
- Stores runtime logs and raw LM Studio request/response payloads in SQLite.
- Preserves dashboard settings and sessions across restarts.
- Stores scheduled reminders in SQLite and wakes Thursday to run an LLM turn when they are due.
- Compresses large `write_file` tool-call content after the model has seen the successful tool result once.

## Requirements

- Windows with PowerShell.
- Python 3.11+.
- Docker Desktop or Docker Engine.
- LM Studio with a model loaded and the local server enabled.

Default LM Studio endpoint:

```text
http://127.0.0.1:1234
```

Default dashboard URL:

```text
http://127.0.0.1:8787
```

## Quick Start

Clone the repository:

```powershell
git clone <your-repo-url> Thursday
cd Thursday
```

Create local config:

```powershell
Copy-Item .env.example .env
```

Create or use a Python environment:

```powershell
python -m venv .conda
.\.conda\Scripts\python.exe -m pip install --upgrade pip
.\.conda\Scripts\python.exe -m pip install -r requirements.txt
```

If you already have `.\.conda\python.exe`, the control script will use it automatically.

Start LM Studio:

1. Open LM Studio.
2. Load your model.
3. Start the local server from the Developer tab.
4. Confirm the model identifier in `.env` matches LM Studio.

Start Thursday:

```powershell
.\scripts\thursday.ps1 start
```

Open the dashboard:

```text
http://127.0.0.1:8787
```

## Control Commands

PowerShell:

```powershell
.\scripts\thursday.ps1 start
.\scripts\thursday.ps1 status
.\scripts\thursday.ps1 logs
.\scripts\thursday.ps1 restart
.\scripts\thursday.ps1 stop
```

Command Prompt:

```bat
scripts\thursday.cmd start
scripts\thursday.cmd status
scripts\thursday.cmd logs
scripts\thursday.cmd restart
scripts\thursday.cmd stop
```

Stop the dashboard and also stop the Docker workspace:

```powershell
.\scripts\thursday.ps1 stop -StopDocker
```

Recreate the Docker workspace from scratch:

```powershell
.\scripts\thursday.ps1 reset-workspace
```

Force recreate without the confirmation prompt:

```powershell
.\scripts\thursday.ps1 reset-workspace -Force
```

There is also a small wrapper:

```powershell
.\scripts\reset-docker-workspace.ps1
```

## Docker Workspace

Thursday creates or starts this container automatically:

```powershell
docker run -dit `
  --name Thursday `
  -w /workspace `
  ubuntu:24.04 `
  bash
```

The agent tools are workspace-relative and operate inside:

```text
docker://Thursday/workspace
```

Generated project files should appear inside the Docker container, not in this host repository.

## Configuration

All runtime config is in `.env`. Commit `.env.example`, not `.env`.

Important settings:

```text
LMSTUDIO_ENDPOINT=http://127.0.0.1:1234
LMSTUDIO_MODEL=qwen3.5-9b
SERVER_HOST=127.0.0.1
SERVER_PORT=8787
DOCKER_CONTAINER_NAME=Thursday
DOCKER_IMAGE=ubuntu:24.04
DOCKER_WORKDIR=/workspace
DEFAULT_CONTEXT_WINDOW=100000
DEFAULT_MAX_TOKENS=32000
WRITE_FILE_SUMMARY_MIN_CHARS=500
WRITE_FILE_SUMMARY_MAX_TOKENS=300
REMINDER_TIMEZONE=UTC
REMINDER_POLL_SECONDS=60
```

`WRITE_FILE_SUMMARY_MIN_CHARS` controls when large `write_file` content is compressed in future context.

`WRITE_FILE_SUMMARY_MAX_TOKENS` controls the output budget for that summary prompt. The prompt text lives in:

```text
prompts/file_write_summary.md
```

## Dashboard

The dashboard includes:

- Session list with persisted sessions.
- Tool permission checkboxes.
- Workspace controls and Docker status.
- Timeline showing orchestrator actions.
- Logs tab with trace, raw LLM logs, HTTP logs, and app logs.
- Settings tab for model parameters and restore defaults.

## Tools

The enabled tools are controlled from the dashboard.

Current tool set:

- `read_file`: read a UTF-8 text file from the Docker workspace.
- `write_file`: create or overwrite a UTF-8 text file in the Docker workspace.
- `edit_file`: replace exact text in an existing file and return a unified diff.
- `list_skills`: list available skill package metadata from `SKILL.md` frontmatter.
- `load_skill`: load one relevant skill package body as a user-role instruction message.
- `read_skill_resource`: read one bundled skill resource from `references/`, `scripts/`, or `assets/`.
- `run_command`: run a Windows PowerShell or cmd command exactly as provided. Use explicit `docker exec -i Thursday bash -lc "cd /workspace && ..."` for Docker workspace inspection, search, tests, builds, and installs.
- `web_search`: search the web for current references.
- `capture_webpage`: capture a 2K or 4K screenshot of a URL, local Windows HTML file, Docker workspace HTML file, or search results page and pass the image to the LLM without DOM text extraction.
- `create_reminder`: create a scheduled reminder that always runs as a future LLM turn.
- `list_reminders`: list stored reminders.
- `update_reminder`: update a reminder schedule or task.
- `delete_reminder`: delete a reminder.
- `create_agent_tool`: create or update a host-side Thursday tool module.

## Reminders

Thursday reminders are app state, so they are stored in the host SQLite database, not in the Docker workspace.

When a reminder is due:

1. The scheduler wakes up.
2. It creates or reuses the persisted `Scheduled reminders` session.
3. It sends the reminder task into the normal orchestrator as a synthetic user message.
4. The LLM can use tools exactly like a normal chat turn.
5. The result appears in the dashboard session history.

There are no notification-only reminders. Every due reminder runs an LLM turn.

Example requests:

```text
Tell me the weather for my configured location daily around 10 o'clock before work.
Tell me the EUR to INR conversion daily around 11 o'clock.
Remind me every Friday at 6pm to review my weekly notes.
Run my standup preparation every 30 minutes.
Check my dashboard every hour at :15.
Remind me on the 1st of every month at 9am to pay rent.
List my reminders.
Delete reminder <id>.
```

Reminder schedule modes:

```text
once            One run on a specific YYYY-MM-DD date at a local time.
every_minutes   Repeats every N minutes. Use interval_minutes.
hourly          Repeats every hour at a specific minute. Use time="15" or time="00:15" for :15.
daily           Repeats every day at a local time. Use time.
weekly          Repeats on selected weekdays at a local time. Use weekdays and time.
monthly         Repeats monthly on a day of month at a local time. Use day_of_month and time.
```

The tool uses the configured system timezone automatically. The model should not pass a timezone field.

Reminder settings:

```text
REMINDER_TIMEZONE=UTC
REMINDER_POLL_SECONDS=60
```

Reminder API:

```text
GET    /api/reminders
POST   /api/reminders
DELETE /api/reminders/{id}
```

## Logs And State

Runtime data is intentionally ignored by Git.

Main runtime files:

```text
logs/thursday_logs.sqlite3
logs/thursday.pid.json
logs/dashboard_preferences.json
logs/server.log
logs/server.err.log
logs/visual_checks/
```

SQLite contains:

- App logs.
- HTTP logs.
- Server logs.
- Raw LM Studio request and response payloads.

Use:

```powershell
.\scripts\thursday.ps1 logs
```

## Project Structure

```text
agent_app/              Python application code
frontend/               Dashboard frontend
prompts/                Agent and summarizer prompts
scripts/                Server entrypoint and control scripts
requirements.txt        Python dependencies
.env.example            Example runtime configuration
.gitignore              Local/runtime ignore rules
```

Key component files:

```text
scripts/server.py                         Starts the dashboard HTTP server
scripts/thursday.ps1                      Start/stop/restart/status/logs/workspace control
agent_app/ARCHITECTURE.md                 Component boundaries and wiring rules
agent_app/runtime/config.py               Loads .env config
agent_app/server/http_server.py           Constructs the HTTP server
agent_app/server/handler.py               Serves API routes and delegates to runtime state
agent_app/runtime/app_state.py            Wires sessions, reminders, preferences, and orchestration
agent_app/orchestration/orchestrator.py   Runs agent turn control flow
agent_app/orchestration/messages.py       Normalizes user/model/image messages
agent_app/orchestration/context_manager.py Manages context pruning and summaries
agent_app/skills/catalog.py               Discovers skill packages and frontmatter metadata
agent_app/skills/packages/                Skill packages with SKILL.md, references, scripts, and assets
agent_app/tools/dispatcher.py             Parses model tool calls and invokes registered tools
agent_app/tools/results.py                Shapes tool results and fallback summaries
agent_app/tools/registry.py               Discovers and executes tool modules
agent_app/llm/lmstudio_client.py          Calls LM Studio
agent_app/storage/session_store.py        Persists sessions in SQLite
agent_app/logging/                        Stores app, HTTP, server, and raw LLM logs
agent_app/utils/                          Small generic helpers split by concern
prompts/operating_instructions.md         Permanent operating instruction message
prompts/tool_operations.md                Always-active tool and skill operating manual
```

## Development

Run a syntax check:

```powershell
.\.conda\python.exe -m compileall agent_app scripts\server.py
```

Start or restart after code changes:

```powershell
.\scripts\thursday.ps1 restart
```

Check status:

```powershell
.\scripts\thursday.ps1 status
```


## Troubleshooting

If the dashboard does not start, run:

```powershell
.\scripts\thursday.ps1 status
.\scripts\thursday.ps1 logs
```

If LM Studio is offline:

- Start the LM Studio local server.
- Check `LMSTUDIO_ENDPOINT` in `.env`.
- Check the model identifier in `.env` and the dashboard settings.

If Docker is not ready:

- Start Docker Desktop.
- Run `docker ps`.
- Run `.\scripts\thursday.ps1 start` again.

If the Docker workspace gets messy:

```powershell
.\scripts\thursday.ps1 reset-workspace
```

This deletes and recreates the `Thursday` container.
