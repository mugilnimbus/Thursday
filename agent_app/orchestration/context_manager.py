from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..runtime.config import AppConfig
from ..llm import LMStudioChatClient
from ..runtime.models import Session
from .prompt_renderer import (
    render_context_summary_prompt,
    render_conversation_summary_prompt,
    render_file_write_summary_prompt,
)
from ..utils import clamp_text, estimate_tokens, parse_arguments, utc_now


class ConversationContextManager:
    """Owns token metrics, conversation pruning, and context-size compression."""

    def __init__(self, config: AppConfig, llm: LMStudioChatClient) -> None:
        self.config = config
        self.llm = llm

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
        output = self.tool_output(result)
        if tool_name != "write_file" or not result.get("ok"):
            return
        content_arg = args.get("content")
        if not isinstance(content_arg, str) or not self.should_summarize_written_content(content_arg):
            return

        summary = self.summarize_written_file_content(
            path=str(args.get("path") or output.get("path") or "unknown"),
            content=content_arg,
            settings=settings,
        )
        self.replace_write_file_content_with_summary(
            session=session,
            assistant_index=assistant_index,
            tool_call_id=tool_call_id,
            path=str(args.get("path") or output.get("path") or "unknown"),
            original_chars=len(content_arg),
            summary=summary,
        )
        session.add_event(
            "context",
            "Compressed successful write_file arguments after full tool observation was stored",
            tool=tool_name,
            path=args.get("path") or output.get("path"),
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

    def prune_context(self, session: Session) -> bool:
        self.refresh_metrics(session)
        if session.token_estimate <= int(session.settings.context_window * self.config.context_prune_ratio):
            return False

        before_tokens = session.token_estimate
        before_messages = len(session.messages)
        previous_summary_chars = len(session.summary)
        session.add_event(
            "context",
            "Context summarizer started",
            token_estimate=before_tokens,
            context_window=session.settings.context_window,
            prune_ratio=self.config.context_prune_ratio,
            message_count=before_messages,
            previous_summary_chars=previous_summary_chars,
        )
        preserved = self.preserved_instruction_messages(session.messages)
        preserved_ids = {id(message) for message in preserved}
        recent_pool = [message for message in session.messages if id(message) not in preserved_ids]
        recent = recent_pool[-14:]
        summary = self.summarize_conversation_context(session)
        session.summary = summary
        session.messages = preserved + [{"role": "user", "content": render_context_summary_prompt(self.config, session.summary)}] + recent
        session.response_chain_valid = False
        session.last_response_id = ""
        session.response_anchor_message_count = 0
        session.response_chain_invalid_reason = "active prompt was summarized"
        self.refresh_metrics(session)
        session_summary_file = self.persist_context_summary_file(session, summary)
        session.add_event(
            "context",
            "Context pruned and compressed; response cache chain invalidated",
            before_tokens=before_tokens,
            after_tokens=session.token_estimate,
            context_window=session.settings.context_window,
            before_messages=before_messages,
            after_messages=len(session.messages),
            preserved_messages=len(preserved),
            recent_messages=len(recent),
            summary_chars=len(summary),
            session_summary_file=str(session_summary_file),
            backup_messages=len(session.message_backup),
        )
        return True

    def persist_context_summary_file(self, session: Session, summary: str) -> Path:
        session_dir = self.config.log_dir / "context_summaries"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / f"{session.id}.md"
        metadata = (
            f"- Generated at: {utc_now()}\n"
            f"- Session id: {session.id}\n"
            f"- Session title: {session.title}\n"
            f"- Current goal: {session.current_goal or 'none'}\n"
            f"- Token estimate after compression: {session.token_estimate}\n\n"
            "## Summary\n\n"
            f"{summary.strip() or 'No summary was produced.'}\n"
        )
        session_body = (
            "# Thursday Context Summary\n\n"
            "This per-session file is automatically overwritten whenever this session is summarized.\n"
            "It is a durable session snapshot for inspection and recovery, not a prompt template.\n\n"
            f"{metadata}"
        )
        session_path.write_text(session_body, encoding="utf-8")
        return session_path

    def preserved_instruction_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        preserved: list[dict[str, Any]] = []
        kept_system = False
        for message in messages:
            role = message.get("role")
            content = str(message.get("content") or "")
            if role == "system" and not kept_system:
                preserved.append(message)
                kept_system = True
                continue
            if role != "user":
                continue
            if (
                "[Thursday Operating Instructions]" in content
                or "[Thursday Always Active Instructions:" in content
                or "[Thursday Always Active Skill:" in content
                or "[Thursday Loaded Skill:" in content
            ):
                preserved.append(message)
        return preserved

    def summarize_conversation_context(self, session: Session) -> str:
        conversation_messages = [
            self.compact_message_for_summary(message)
            for message in session.messages[1:]
            if message.get("role") != "system"
        ]
        conversation = json.dumps(conversation_messages, ensure_ascii=False, indent=2)
        conversation = clamp_text(conversation, self.summary_input_limit(session))
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

    def compact_message_for_summary(self, message: dict[str, Any]) -> dict[str, Any]:
        compact: dict[str, Any] = {}
        for key, value in message.items():
            if key == "content":
                compact[key] = clamp_text(str(value or ""), 6000)
                continue
            if key == "tool_calls":
                compact[key] = self.compact_tool_calls(value)
                continue
            if key == "images":
                compact[key] = "[image attachment metadata omitted from summary input]"
                continue
            compact[key] = value
        return compact

    def compact_tool_calls(self, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        compact_calls: list[Any] = []
        for call in value[:12]:
            if not isinstance(call, dict):
                compact_calls.append(call)
                continue
            compact_call = dict(call)
            function = compact_call.get("function")
            if isinstance(function, dict):
                function = dict(function)
                if isinstance(function.get("arguments"), str):
                    function["arguments"] = clamp_text(function["arguments"], 6000)
                compact_call["function"] = function
            compact_calls.append(compact_call)
        if len(value) > 12:
            compact_calls.append({"truncated_tool_calls": len(value) - 12})
        return compact_calls

    def summary_input_limit(self, session: Session) -> int:
        context_window = max(8000, int(session.settings.context_window or self.config.default_context_window))
        return min(max(context_window // 2, 12000), 48000)

    def refresh_metrics(self, session: Session) -> None:
        joined = json.dumps(session.messages)
        session.token_estimate = estimate_tokens(joined)
        session.updated_at = utc_now()

    def tool_output(self, result: dict[str, Any]) -> dict[str, Any]:
        output = result.get("output")
        return output if isinstance(output, dict) else {}

