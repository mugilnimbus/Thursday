# Thursday Tool Modules

Drop workspace tools into `workspace/tool_name.py` and system/meta tools into `system/tool_name.py`. The registry auto-discovers tool modules in both folders and hot-reloads when files change.

Thursday also has a `create_agent_tool` system tool that can write new system tool modules for itself. That tool validates the name and Python syntax, writes the module, and makes the new tool available on the next tool-list refresh or model turn.

## Unified Tool API

Every model-facing tool uses the same call envelope:

```json
{"input": {"field": "value"}, "meta": {}}
```

The dispatcher parses this envelope once. Individual tools receive only the parsed `input` dictionary and should not implement their own tool-call parser.

Every tool result returned to the LLM uses the same response envelope:

```json
{
  "ok": true,
  "tool": "tool_name",
  "output": {},
  "error": null,
  "meta": {
    "api_version": "2026-06-02",
    "input": {},
    "duration_ms": 0,
    "category": "workspace",
    "requires_container": false
  }
}
```

Tool modules may return a simple legacy dictionary such as `{"ok": true, "path": "file.txt"}`. The registry wraps it into the unified envelope. New tools should still think in terms of `input` and `output`: put successful data in normal fields and failures in `{"ok": false, "error": "message"}`.

Each tool module must define:

```python
TOOL_NAME = "tool_name"
TOOL_ORDER = 100
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Clear one-line description for the model.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def run(context, args):
    return {"ok": True}
```

Use `REQUIRES_CONTAINER = True` only for tools that truly must run inside the Ubuntu Docker workspace. Those tools will be blocked automatically when the `Thursday` container is not running.

`run_command` is intentionally different: it is a general Windows host command runner. It does not route into Docker automatically. If the model wants to work inside Docker, the model must include the full `docker exec ...` command in the `command` argument.

Tool-call routing is intentionally split from orchestration:

- `dispatcher.py`: parses raw model tool calls, resolves the requested tool name and arguments, and invokes the registry.
- `results.py`: formats raw tool output into LLM observations, image attachments, fallback summaries, modified-file markers, and error metadata.

Shared helpers live one package level up:

- `context.py`: Docker execution, workspace path safety, browser helpers, and reminder context.
- `base.py`: the `ToolSpec` structure used by the registry.
- `parsers.py`: small HTML parsers for web tools.

Tool paths must stay workspace-relative. Do not let tool code write agent-generated project files into the host app repository unless the tool is explicitly meant to manage the host app.
