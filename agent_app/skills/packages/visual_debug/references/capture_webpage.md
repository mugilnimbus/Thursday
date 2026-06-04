# capture_webpage Reference

Use this reference only when visual capture fails, the target path is ambiguous, or the model is unsure which input field to use.

## Inputs
- `url`: Use for HTTP, HTTPS, or file URLs that Chrome can open directly.
- `local_path`: Use for absolute Windows host HTML files such as `C:\Users\Name\Desktop\app\index.html`.
- `workspace_path`: Use only for HTML files that live inside the Docker workspace.
- `query`: Use only when the user asks for a search-result screenshot and no URL/path is known.

## Recovery
- If Docker reports `/workspace/...` is missing and the user gave `C:\...`, retry with `local_path`.
- If the screenshot is blank, increase `wait_ms` or open a dev-server URL instead of a static file.
- If small UI details matter, use `resolution: "4k"` or explicit viewport dimensions.
- Never treat DOM text extraction as visual verification.
