# Thursday

Thursday is a local software-building agent with a web dashboard for chat, tool control, monitoring, timelines, settings, and raw LM Studio logs.

The agent uses a local LM Studio server through the OpenAI-compatible chat-completions API and executes project tools only inside an Ubuntu Docker workspace.

## What It Does

- Chats with a local LM Studio model.
- Lets the model call tools for files, commands, web search, and webpage inspection.
- Runs workspace tools inside the Docker container named `Thursday`.
- Shows session history, timeline events, tool permissions, logs, raw LLM input/output, and settings in the dashboard.
- Stores runtime logs and raw LM Studio request/response payloads in SQLite.
- Preserves dashboard settings and sessions across restarts.
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

- `inspect_workspace`: inspect files and tree inside the Docker workspace.
- `read_file`: read a UTF-8 text file from the Docker workspace.
- `write_file`: create or overwrite a UTF-8 text file in the Docker workspace.
- `edit_file`: replace exact text in an existing file and return a unified diff.
- `search_workspace`: search text files in the Docker workspace.
- `run_command`: run a Bash command inside `/workspace`.
- `web_search`: search the web for current references.
- `inspect_webpage`: open a URL or workspace HTML file in headless Chrome and capture diagnostics.

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
agent_app/              Backend application code
prompts/                Agent and summarizer prompts
scripts/                Server entrypoint and control scripts
web/                    Dashboard frontend
requirements.txt        Python dependencies
.env.example            Example runtime configuration
.gitignore              Local/runtime ignore rules
```

Key backend files:

```text
scripts/server.py              Starts the dashboard HTTP server
scripts/thursday.ps1           Start/stop/restart/status/logs/workspace control
agent_app/config.py            Loads .env config
agent_app/http_app.py          Serves API and dashboard files
agent_app/orchestrator.py      Runs the agent loop
agent_app/tools.py             Defines and executes tools
agent_app/llm_client.py        Calls LM Studio
agent_app/session_store.py     Persists sessions in SQLite
agent_app/sqlite_logging.py    Stores app, HTTP, server, and raw LLM logs
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
