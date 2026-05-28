from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any, Callable

from .config import AppConfig
from .llm_client import LMStudioChatClient
from .models import Session
from .prompts import (
    render_context_summary_prompt,
    render_conversation_summary_prompt,
    render_file_write_summary_prompt,
    render_system_prompt,
)
from .tools import ToolRegistry
from .reminders import ReminderStore
from .utils import clamp_text, estimate_tokens, parse_arguments, utc_now


class AgentOrchestrator:
    def __init__(self, config: AppConfig, reminder_store: ReminderStore | None = None) -> None:
        self.config = config
        self.tools = ToolRegistry(config, reminder_store=reminder_store)
        self.llm = LMStudioChatClient(config)
        self.session_update_callback: Callable[[Session], None] | None = None

    def save_session(self, session: Session) -> None:
        if not self.session_update_callback:
            return
        try:
            self.session_update_callback(session)
        except Exception:
            pass

    def run_turn(self, session: Session, user_message: str, settings_patch: dict[str, Any] | None = None) -> None:
        with session.lock:
            if settings_patch:
                for key, value in settings_patch.items():
                    if hasattr(session.settings, key):
                        setattr(session.settings, key, value)
            session.status = "running"
            session.current_goal = user_message
            if session.title == "New session":
                session.title = user_message[:48] or "Agent session"
            session.visible_messages.append({"role": "user", "content": user_message, "timestamp": utc_now()})
            if not session.messages:
                session.messages = [{"role": "system", "content": self.system_prompt(session)}]
            else:
                session.messages[0] = {"role": "system", "content": self.system_prompt(session)}
            session.messages.append({"role": "user", "content": user_message})
            session.add_event("user", "User message received", chars=len(user_message))
            self.prune_context(session)
            self.save_session(session)

        try:
            last_tool_name = ""
            last_tool_result: dict[str, Any] | None = None
            empty_after_tool_retries = 0
            pending_write_file_compressions: list[dict[str, Any]] = []
            for step in range(1, session.settings.max_steps + 1):
                with session.lock:
                    session.add_event("llm", f"Calling LM Studio, step {step}", model=session.settings.model)
                    messages = list(session.messages)
                    settings = session.settings
                    enabled_tools = list(session.settings.enabled_tools)
                raw = self.llm.chat(messages, settings, self.tools.definitions(enabled_tools))
                choice = self.first_choice(raw)
                finish_reason = str(choice.get("finish_reason") or "")
                assistant_message = choice.get("message", {}) if isinstance(choice.get("message"), dict) else {}
                normalized = self.normalize_assistant_message(assistant_message)
                if raw.get("usage"):
                    normalized["usage"] = raw["usage"]
                content = normalized.get("content", "") or ""
                content, final_answer_requested = self.strip_final_answer_marker(str(content))
                normalized["content"] = content
                thinking_content = normalized.get("thinking") or ""
                tool_calls = normalized.get("tool_calls", [])
                visible_content = content.strip()
                thinking_content = str(thinking_content).strip()

                with session.lock:
                    assistant_index = len(session.messages)
                    session.messages.append(normalized)
                    if visible_content:
                        session.visible_messages.append({
                            "role": "assistant",
                            "content": visible_content,
                            "timestamp": utc_now(),
                            "thinking_hidden": bool(thinking_content),
                        })
                    if thinking_content:
                        session.add_event("thinking", "Model produced <think> content", chars=len(thinking_content))
                    session.add_event(
                        "llm_response",
                        "LM Studio response received",
                        finish_reason=finish_reason,
                        visible_chars=len(visible_content),
                        thinking_chars=len(thinking_content),
                        tool_calls=len(tool_calls),
                        final_answer_requested=final_answer_requested,
                    )
                    self.save_session(session)

                if not tool_calls:
                    if not visible_content and last_tool_result is not None:
                        if empty_after_tool_retries < 1 and step < session.settings.max_steps:
                            empty_after_tool_retries += 1
                            with session.lock:
                                if assistant_index < len(session.messages):
                                    session.messages.pop(assistant_index)
                                session.add_event("retry", "Model returned empty text after tool result; retrying the same LLM input", tool=last_tool_name)
                                self.save_session(session)
                            continue
                        if pending_write_file_compressions:
                            with session.lock:
                                for pending in pending_write_file_compressions:
                                    self.compress_successful_write_file_call_after_observation(session=session, **pending)
                                pending_write_file_compressions.clear()
                                self.prune_context(session)
                                self.save_session(session)
                        fallback_content = self.fallback_tool_response(last_tool_name, last_tool_result)
                        with session.lock:
                            session.messages.append({"role": "assistant", "content": fallback_content})
                            session.visible_messages.append({
                                "role": "assistant",
                                "content": fallback_content,
                                "timestamp": utc_now(),
                                "generated_fallback": True,
                            })
                            session.add_event("fallback", "Model returned empty text after tool result; generated a tool-result summary", tool=last_tool_name)
                    elif not visible_content:
                        fallback_content = self.fallback_empty_response()
                        with session.lock:
                            session.messages.append({"role": "assistant", "content": fallback_content})
                            session.visible_messages.append({
                                "role": "assistant",
                                "content": fallback_content,
                                "timestamp": utc_now(),
                                "generated_fallback": True,
                            })
                            session.add_event("fallback", "Model returned no visible assistant content")
                    elif pending_write_file_compressions:
                        with session.lock:
                            for pending in pending_write_file_compressions:
                                self.compress_successful_write_file_call_after_observation(session=session, **pending)
                            pending_write_file_compressions.clear()
                            self.prune_context(session)
                            self.save_session(session)
                    with session.lock:
                        session.status = "idle"
                        session.add_event("done", "Agent turn completed")
                        self.refresh_metrics(session)
                        self.save_session(session)
                    return

                if pending_write_file_compressions:
                    with session.lock:
                        for pending in pending_write_file_compressions:
                            self.compress_successful_write_file_call_after_observation(session=session, **pending)
                        pending_write_file_compressions.clear()
                        self.prune_context(session)
                        self.save_session(session)

                for call in tool_calls:
                    name = call.get("function", {}).get("name") or call.get("name")
                    raw_args = call.get("function", {}).get("arguments", "{}") or call.get("arguments", "{}")
                    args = parse_arguments(raw_args)
                    tool_call_id = call.get("id") or f"call_{uuid.uuid4().hex[:10]}"
                    with session.lock:
                        session.tool_counts[name] = session.tool_counts.get(name, 0) + 1
                        session.add_event("tool_call", f"Invoking {name}", tool=name, args=args)

                    result = self.tools.invoke(name, args, session.settings.enabled_tools)
                    empty_after_tool_retries = 0
                    last_tool_name = str(name or "")
                    last_tool_result = result
                    result_text = json.dumps(result, indent=2)
                    observation = result_text

                    with session.lock:
                        if name in {"write_file", "edit_file"} and result.get("ok") and result.get("path"):
                            rel = result["path"]
                            if rel not in session.modified_files:
                                session.modified_files.append(rel)
                        if not result.get("ok"):
                            error = result.get("error") or result.get("stderr") or result_text
                            session.recent_errors.append(clamp_text(str(error), session.settings.context_window))
                        session.messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "content": observation,
                        })
                        session.add_event("tool_result", f"{name} returned {'success' if result.get('ok') else 'error'}", tool=name, ok=result.get("ok"), result=result)
                        if (
                            name == "write_file"
                            and result.get("ok")
                            and isinstance(args.get("content"), str)
                            and self.should_summarize_written_content(str(args.get("content")))
                        ):
                            pending_write_file_compressions.append({
                                "assistant_index": assistant_index,
                                "tool_call_id": tool_call_id,
                                "tool_name": str(name or ""),
                                "args": args,
                                "result": result,
                                "settings": session.settings,
                            })
                        else:
                            self.prune_context(session)
                        self.save_session(session)

            with session.lock:
                session.status = "idle"
                session.add_event("stopped", "Stopped after max agent steps", max_steps=session.settings.max_steps)
                session.visible_messages.append({
                    "role": "assistant",
                    "content": f"Stopped after {session.settings.max_steps} agent steps. Increase max steps if you want me to continue.",
                    "timestamp": utc_now(),
                })
                self.refresh_metrics(session)
                self.save_session(session)
        except Exception as exc:
            with session.lock:
                session.status = "error"
                session.recent_errors.append(str(exc))
                session.add_event("error", "Agent run failed", error=str(exc))
                session.visible_messages.append({"role": "assistant", "content": f"Agent error: {exc}", "timestamp": utc_now()})
                self.refresh_metrics(session)
                self.save_session(session)

    def system_prompt(self, session: Session) -> str:
        tool_names = ", ".join(item["function"]["name"] for item in self.tools.definitions(session.settings.enabled_tools))
        return render_system_prompt(self.config, tool_names, self.project_state(session))

    def fallback_tool_response(self, name: str, result: dict[str, Any]) -> str:
        if not result.get("ok"):
            error = result.get("error") or result.get("stderr") or "Unknown tool error"
            return f"`{name}` failed: {error}"

        if name == "inspect_workspace":
            files = result.get("files") or []
            tree = result.get("tree") or "(empty workspace)"
            formatted = "\n".join(f"- `{item}`" for item in files)
            if not files:
                return f"Workspace tree:\n```text\n{tree}\n```"
            return f"Workspace tree:\n```text\n{tree}\n```\n\nFiles:\n{formatted}"

        if name == "read_file":
            path = result.get("path", "file")
            content = clamp_text(str(result.get("content", "")), self.config.default_context_window)
            suffix = "\n\nOutput was truncated." if result.get("truncated") else ""
            return f"Read `{path}`:\n```text\n{content}\n```{suffix}"

        if name == "search_workspace":
            matches = result.get("matches") or []
            if not matches:
                return "No matches found in the workspace."
            lines = [f"- `{item.get('path')}`:{item.get('line')} {item.get('preview')}" for item in matches[:20]]
            return "Search matches:\n" + "\n".join(lines)

        if name == "run_command":
            stdout = str(result.get("stdout") or "").strip()
            stderr = str(result.get("stderr") or "").strip()
            exit_code = result.get("exit_code")
            pieces = [f"`run_command` finished with exit code `{exit_code}`."]
            if stdout:
                pieces.append(f"stdout:\n```text\n{stdout}\n```")
            if stderr:
                pieces.append(f"stderr:\n```text\n{stderr}\n```")
            return "\n\n".join(pieces)

        if name in {"write_file", "edit_file"}:
            path = result.get("path", "workspace")
            return f"`{name}` completed successfully for `{path}`."

        if name == "web_search":
            results = result.get("results") or []
            if not results:
                return "No web results found."
            lines = [f"- [{item.get('title')}]({item.get('url')})" for item in results[:10]]
            return "Web results:\n" + "\n".join(lines)

        if name == "inspect_webpage":
            diagnostics = result.get("diagnostics") or []
            lines = [
                "Visual check completed.",
                f"- Title: `{result.get('title') or '(none)'}`",
                f"- Screenshot: `{result.get('screenshot_path')}`",
            ]
            visible_text = str(result.get("visible_text") or "").strip()
            if visible_text:
                lines.append(f"- Visible text preview: {clamp_text(visible_text, self.config.default_context_window)}")
            if diagnostics:
                lines.append("- Diagnostics:\n" + "\n".join(f"  - {item}" for item in diagnostics[:8]))
            return "\n".join(lines)

        return f"`{name}` completed successfully:\n```json\n{clamp_text(json.dumps(result, indent=2), self.config.default_context_window)}\n```"

    def project_state(self, session: Session) -> dict[str, str]:
        recent_errors = "\n".join(f"- {error}" for error in session.recent_errors[-5:]) or "none"
        modified_files = "\n".join(f"- {path}" for path in session.modified_files[-20:]) or "none"
        return {
            "current_goal": session.current_goal or "none",
            "context_summary": session.summary or "none",
            "modified_files": modified_files,
            "recent_errors": recent_errors,
            "tool_counts": json.dumps(session.tool_counts, sort_keys=True) if session.tool_counts else "{}",
            "session_status": session.status,
            "token_estimate": str(session.token_estimate),
            "context_window": str(session.settings.context_window),
        }

    def first_choice(self, raw: dict[str, Any]) -> dict[str, Any]:
        choices = raw.get("choices") if isinstance(raw, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return choices[0]
        return {}

    def normalize_assistant_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content_text = self.message_content_to_text(message.get("content"))
        content_without_thinking, inline_thinking = self.extract_think_blocks(content_text)

        normalized = {"role": "assistant", "content": content_without_thinking.strip()}
        if inline_thinking.strip():
            normalized["thinking"] = inline_thinking.strip()
        if message.get("tool_calls"):
            normalized["tool_calls"] = message["tool_calls"]
        return normalized

    def strip_final_answer_marker(self, content: str) -> tuple[str, bool]:
        marker = "</Final_answer>"
        if marker not in content:
            return content, False
        return content.replace(marker, "").strip(), True

    def message_content_to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                    elif text is not None:
                        parts.append(json.dumps(text, ensure_ascii=False))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return json.dumps(value, ensure_ascii=False)

    def extract_think_blocks(self, content: str) -> tuple[str, str]:
        if "<think>" not in content:
            return content, ""

        visible_parts: list[str] = []
        thinking_parts: list[str] = []
        cursor = 0
        while True:
            start = content.find("<think>", cursor)
            if start == -1:
                visible_parts.append(content[cursor:])
                break
            visible_parts.append(content[cursor:start])
            end = content.find("</think>", start + len("<think>"))
            if end == -1:
                thinking_parts.append(content[start + len("<think>") :])
                break
            thinking_parts.append(content[start + len("<think>") : end])
            cursor = end + len("</think>")

        visible = "".join(visible_parts).strip()
        thinking = "\n\n".join(part.strip() for part in thinking_parts if part.strip())
        return visible, thinking

    def fallback_empty_response(self) -> str:
        return (
            "LM Studio returned an empty assistant message with no tool call. "
            "Open Logs > Raw to inspect the full endpoint response."
        )

    def should_summarize_written_content(self, content: str) -> bool:
        return len(content) >= self.config.write_file_summary_min_chars

    def compress_successful_write_file_call_after_observation(
        self,
        session: Session,
        assistant_index: int,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        settings: Any,
    ) -> None:
        if tool_name != "write_file" or not result.get("ok"):
            return
        content_arg = args.get("content")
        if not isinstance(content_arg, str) or not self.should_summarize_written_content(content_arg):
            return

        summary = self.summarize_written_file_content(
            path=str(args.get("path") or result.get("path") or "unknown"),
            content=content_arg,
            settings=settings,
        )
        self.replace_write_file_content_with_summary(
            session=session,
            assistant_index=assistant_index,
            tool_call_id=tool_call_id,
            path=str(args.get("path") or result.get("path") or "unknown"),
            original_chars=len(content_arg),
            summary=summary,
        )
        session.add_event(
            "context",
            "Compressed successful write_file arguments after full tool observation was stored",
            tool=tool_name,
            path=args.get("path") or result.get("path"),
            original_chars=len(content_arg),
        )

    def summarize_written_file_content(self, path: str, content: str, settings: Any) -> str:
        prompt = render_file_write_summary_prompt(self.config, path, content)
        summary_settings = replace(
            settings,
            max_tokens=min(settings.max_tokens, self.config.write_file_summary_max_tokens, settings.context_window),
            enable_thinking=False,
        )
        try:
            raw = self.llm.chat([{"role": "user", "content": prompt}], summary_settings, [])
            message = raw.get("choices", [{}])[0].get("message", {})
            summary = (message.get("content") or "").strip()
            if summary:
                return summary
        except Exception as exc:
            return (
                "LLM file-content summary failed, so the original content was omitted to protect context size. "
                f"Summary error: {exc}"
            )
        return "LLM file-content summary was empty; original content was omitted to protect context size."

    def replace_write_file_content_with_summary(
        self,
        session: Session,
        assistant_index: int,
        tool_call_id: str,
        path: str,
        original_chars: int,
        summary: str,
    ) -> None:
        if assistant_index < 0 or assistant_index >= len(session.messages):
            return
        assistant_message = session.messages[assistant_index]
        tool_calls = assistant_message.get("tool_calls") or []
        for call in tool_calls:
            call_id = call.get("id")
            function = call.get("function", {})
            name = function.get("name") or call.get("name")
            if name != "write_file":
                continue
            if call_id and call_id != tool_call_id:
                continue
            raw_args = function.get("arguments", "{}") or call.get("arguments", "{}")
            parsed_args = parse_arguments(raw_args)
            if path and parsed_args.get("path") and parsed_args.get("path") != path:
                continue
            parsed_args["content"] = (
                "[omitted by Thursday context manager after successful write_file; "
                "see content_summary for the detailed brief]"
            )
            parsed_args["content_summary"] = summary
            parsed_args["original_content_chars"] = original_chars
            parsed_args["context_management"] = "Full write_file content was replaced with this LLM-written brief."
            if "function" in call:
                call["function"]["arguments"] = json.dumps(parsed_args, ensure_ascii=False)
                call["id"] = tool_call_id
            else:
                call["arguments"] = json.dumps(parsed_args, ensure_ascii=False)
                call["id"] = tool_call_id
            return

    def prune_context(self, session: Session) -> None:
        self.refresh_metrics(session)
        if session.token_estimate <= int(session.settings.context_window * self.config.context_prune_ratio):
            return

        system = session.messages[:1]
        recent = session.messages[-14:]
        session.summary = self.summarize_conversation_context(session)
        session.messages = system + [{"role": "system", "content": render_context_summary_prompt(self.config, session.summary)}] + recent
        session.add_event("context", "Context pruned and compressed", token_estimate=session.token_estimate)
        self.refresh_metrics(session)

    def summarize_conversation_context(self, session: Session) -> str:
        conversation_messages = [message for message in session.messages[1:] if message.get("role") != "system"]
        conversation = json.dumps(conversation_messages, ensure_ascii=False, indent=2)
        prompt = render_conversation_summary_prompt(self.config, session.summary, conversation)
        summary_settings = replace(
            session.settings,
            max_tokens=min(session.settings.max_tokens, self.config.context_summary_max_tokens, session.settings.context_window),
            enable_thinking=False,
        )
        try:
            raw = self.llm.chat([{"role": "user", "content": prompt}], summary_settings, [])
            message = raw.get("choices", [{}])[0].get("message", {})
            summary = (message.get("content") or "").strip()
            if summary:
                return summary
        except Exception as exc:
            fallback = f"LLM context summary failed: {exc}"
        else:
            fallback = "LLM context summary was empty."

        compact_notes = [
            fallback,
            f"Current goal: {session.current_goal}",
            f"Modified files: {', '.join(session.modified_files[-20:]) or 'none'}",
            f"Recent errors: {' | '.join(session.recent_errors[-5:]) or 'none'}",
            f"Tool counts: {json.dumps(session.tool_counts)}",
        ]
        if session.summary:
            compact_notes.insert(1, f"Previous summary: {session.summary}")
        return "\n".join(compact_notes)

    def refresh_metrics(self, session: Session) -> None:
        joined = json.dumps(session.messages)
        session.token_estimate = estimate_tokens(joined)
        session.updated_at = utc_now()
