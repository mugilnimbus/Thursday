from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ..runtime.config import AppConfig
from ..storage.image_store import image_url
from ..utils import clamp_text
from .dispatcher import ToolExecution


@dataclass(frozen=True)
class ToolObservation:
    name: str
    tool_call_id: str
    args: dict[str, Any]
    raw_result: dict[str, Any]
    stored_result: dict[str, Any]
    observation: str
    llm_images: list[dict[str, str]]
    modified_path: str = ""
    error_text: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.stored_result.get("ok"))


class ToolResultPresenter:
    """Turns tool executions into session updates, LLM observations, and user fallbacks."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def build_observation(self, execution: ToolExecution) -> ToolObservation:
        stored_result = self.strip_llm_images(execution.result)
        result_text = clamp_text(
            json.dumps(self.llm_visible_result(execution.call.name, stored_result), indent=2),
            self.effective_observation_limit(),
        )
        output = self.output(stored_result)
        return ToolObservation(
            name=execution.call.name,
            tool_call_id=execution.call.id,
            args=execution.call.args,
            raw_result=execution.result,
            stored_result=stored_result,
            observation=result_text,
            llm_images=self.extract_llm_images(execution.result),
            modified_path=self.modified_path(execution.call.name, output),
            error_text=self.error_text(stored_result),
        )

    def compact_stored_result(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        return self.llm_visible_result(tool_name, result)

    def llm_visible_result(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        result = self.compact_result_payload(result)
        if tool_name != "load_skill" or not result.get("ok"):
            return result
        output = self.output(result)
        compact_output = {
            key: value
            for key, value in output.items()
            if key not in {"instruction_message"}
        }
        compact_output["instruction_message_appended_as_user_message"] = True
        compact = dict(result)
        compact["output"] = compact_output
        return compact

    def compact_result_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        compact = self.compact_value(result)
        if isinstance(compact, dict):
            return compact
        return {"ok": False, "error": "Tool result had an unexpected non-object payload.", "output": {"value": compact}}

    def compact_value(self, value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            return clamp_text(value, self.string_limit_for(depth))
        if isinstance(value, dict):
            if depth >= 8:
                return {"truncated": "nested object omitted after depth limit"}
            compact_dict: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= 80:
                    compact_dict["truncated_keys"] = f"{len(value) - index} additional keys omitted"
                    break
                compact_dict[str(key)] = self.compact_value(item, depth + 1)
            return compact_dict
        if isinstance(value, list):
            if depth >= 8:
                return [f"nested list omitted after depth limit; original length {len(value)}"]
            limit = 24 if depth <= 1 else 8
            compact_list = [self.compact_value(item, depth + 1) for item in value[:limit]]
            if len(value) > limit:
                compact_list.append(f"... truncated {len(value) - limit} additional items")
            return compact_list
        return value

    def string_limit_for(self, depth: int) -> int:
        base = self.effective_output_limit()
        if depth <= 2:
            return base
        return min(base, 1000)

    def effective_output_limit(self) -> int:
        return min(max(self.config.tool_max_output_chars, 1000), 16000)

    def effective_error_limit(self) -> int:
        return min(max(self.config.tool_max_error_chars, 1000), 6000)

    def effective_observation_limit(self) -> int:
        return min(max(self.config.tool_observation_max_chars, 2000), 24000)

    def tool_message(self, observation: ToolObservation) -> dict[str, Any]:
        message = {
            "role": "tool",
            "tool_call_id": observation.tool_call_id,
            "name": observation.name,
            "content": observation.observation,
        }
        if observation.llm_images:
            message["images"] = observation.llm_images
        return message

    def write_file_compression_payload(
        self,
        observation: ToolObservation,
        should_summarize: Callable[[str], bool],
    ) -> dict[str, Any] | None:
        if observation.name != "write_file" or not observation.ok:
            return None
        content = observation.args.get("content")
        if not isinstance(content, str) or not should_summarize(content):
            return None
        return {
            "tool_call_id": observation.tool_call_id,
            "tool_name": observation.name,
            "args": observation.args,
            "result": observation.stored_result,
        }

    def fallback_tool_response(self, observation: ToolObservation) -> str:
        result = observation.stored_result
        output = self.output(result)
        if not result.get("ok"):
            recovery_hint = output.get("recovery_hint")
            suffix = f"\n\nNext step: {recovery_hint}" if isinstance(recovery_hint, str) and recovery_hint.strip() else ""
            return f"`{observation.name}` failed: {self.error_text(result) or 'Unknown tool error'}{suffix}"

        summary = self.output_summary(output)
        if summary:
            return f"`{observation.name}` completed successfully.\n\n{summary}"
        return f"`{observation.name}` completed successfully:\n```json\n{clamp_text(json.dumps(output, indent=2), self.effective_observation_limit())}\n```"

    def fallback_empty_response(self) -> str:
        return (
            "LM Studio returned an empty assistant message with no tool call. "
            "Open Logs > Raw to inspect the full endpoint response."
        )

    def extract_llm_images(self, result: dict[str, Any]) -> list[dict[str, str]]:
        images = self.output(result).get("llm_images")
        if not isinstance(images, list):
            return []
        normalized: list[dict[str, str]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            path = str(image.get("path") or "")
            if not path:
                continue
            normalized.append({
                "source": str(image.get("source") or "tool"),
                "path": path,
                "mime_type": str(image.get("mime_type") or "image/png"),
                "url": str(image.get("url") or image_url(path)),
            })
        return normalized

    def strip_llm_images(self, result: dict[str, Any]) -> dict[str, Any]:
        output = self.output(result)
        if "llm_images" not in output:
            return result
        stripped = dict(result)
        stripped_output = dict(output)
        images = self.extract_llm_images(result)
        stripped_output["llm_images"] = [
            {
                "source": image.get("source", "tool"),
                "path": image.get("path", ""),
                "mime_type": image.get("mime_type", "image/png"),
                "url": image.get("url", ""),
                "attached_to_next_llm_request": True,
            }
            for image in images
        ]
        stripped["output"] = stripped_output
        return stripped

    def modified_path(self, name: str, output: dict[str, Any]) -> str:
        if name not in {"write_file", "edit_file"} or not output.get("path"):
            return ""
        return str(output["path"])

    def error_text(self, result: dict[str, Any]) -> str:
        if result.get("ok"):
            return ""
        error = result.get("error")
        if isinstance(error, dict):
            return clamp_text(str(error.get("message") or json.dumps(error, indent=2)), self.effective_error_limit())
        return clamp_text(str(error or json.dumps(result, indent=2)), self.effective_error_limit())

    def output(self, result: dict[str, Any]) -> dict[str, Any]:
        output = result.get("output")
        return output if isinstance(output, dict) else {}

    def output_summary(self, output: dict[str, Any]) -> str:
        for key in ("summary", "message"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if isinstance(output.get("content"), str):
            return self.content_output_summary(output)
        if "stdout" in output or "stderr" in output:
            pieces: list[str] = []
            if output.get("exit_code") is not None:
                pieces.append(f"Exit code: `{output.get('exit_code')}`")
            stdout = str(output.get("stdout") or "").strip()
            stderr = str(output.get("stderr") or "").strip()
            if stdout:
                pieces.append(f"stdout:\n```text\n{clamp_text(stdout, self.effective_output_limit())}\n```")
            if stderr:
                pieces.append(f"stderr:\n```text\n{clamp_text(stderr, self.effective_error_limit())}\n```")
            return "\n\n".join(pieces)
        return ""

    def content_output_summary(self, output: dict[str, Any]) -> str:
        content = str(output.get("content") or "")
        resource_path = str(output.get("resource_path") or output.get("path") or "content")
        skill_name = str(output.get("skill_name") or "").strip()
        heading = self.first_markdown_heading(content)
        pieces: list[str] = []
        if skill_name:
            pieces.append(f"Loaded `{resource_path}` from skill `{skill_name}`.")
        else:
            pieces.append(f"Loaded `{resource_path}`.")
        if heading:
            pieces.append(f"First heading: `{heading}`")
        excerpt = clamp_text(content.strip(), min(2000, self.config.default_context_window))
        if excerpt:
            pieces.append(f"Content excerpt:\n```markdown\n{excerpt}\n```")
        return "\n\n".join(pieces)

    def first_markdown_heading(self, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped
        return ""

