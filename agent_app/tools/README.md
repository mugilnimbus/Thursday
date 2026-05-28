# Thursday Tool Modules

Drop a new `tool_name.py` file into this folder. The registry auto-discovers every non-private Python file in this package and hot-reloads when files change.

Thursday also has a `create_agent_tool` tool that can write these modules for itself. That tool validates the name and Python syntax, writes the module here, and makes the new tool available on the next tool-list refresh or model turn.

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

Use `REQUIRES_CONTAINER = True` for tools that must run inside the Ubuntu Docker workspace. Those tools will be blocked automatically when the `Thursday` container is not running.

Shared helpers live in:

- `context.py`: Docker execution, workspace path safety, browser helpers.
- `base.py`: the `ToolSpec` structure used by the registry.
- `parsers.py`: small HTML parsers for web tools.

Tool paths must stay workspace-relative. Do not let tool code write agent-generated project files into the host app repository unless the tool is explicitly meant to manage the host app.
