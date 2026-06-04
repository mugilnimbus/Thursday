from __future__ import annotations

import json
from typing import Any, Callable

from ..runtime.config import AppConfig
from ..llm import LMStudioChatClient
from ..runtime.models import Session
from ..storage.reminder_store import ReminderStore
from ..tools import ToolRegistry
from ..tools.dispatcher import ToolCallDispatcher
from ..tools.results import ToolObservation, ToolResultPresenter
from ..utils import clamp_text, utc_now
from .context_manager import ConversationContextManager
from .conversation_state import (
    append_conversation_message,
    clone_message,
    consume_tool_images_after_llm,
    ensure_message_backup,
)
from .instructions import (
    append_loaded_skill_message,
    ensure_core_instruction_messages,
    operating_instructions_message,
    system_prompt,
    tool_operations_message,
)
from .messages import ConversationMessageBuilder
from .response_chain import (
    invalidate_response_chain,
    response_chain_for_call,
    update_response_chain_after_llm,
)

VISUAL_REFUSAL_PHRASES = (
    "cannot visually inspect",
    "can't visually inspect",
    "cannot inspect screenshots",
    "can't inspect screenshots",
    "cannot see the screenshot",
    "can't see the screenshot",
    "cannot see their contents",
    "only have file paths",
    "only have paths",
    "returned paths to the captured images",
    "view the screenshot files directly",
    "use a tool that can parse/display image",
    "do not have the ability to visually inspect",
    "don't have the ability to visually inspect",
)


class AgentOrchestrator:
    """Coordinates an agent turn without owning tool parsing or tool routing."""

    def __init__(self, config: AppConfig, reminder_store: ReminderStore | None = None) -> None:
        self.config = config
        self.tools = ToolRegistry(config, reminder_store=reminder_store)
        self.dispatcher = ToolCallDispatcher(self.tools)
        self.tool_results = ToolResultPresenter(config)
        self.llm = LMStudioChatClient(config)
        self.messages = ConversationMessageBuilder()
        self.context = ConversationContextManager(config, self.llm)
        self.session_update_callback: Callable[[Session], None] | None = None

    def save_session(self, session: Session) -> None:
        if not self.session_update_callback:
            return
        try:
            self.session_update_callback(session)
        except Exception:
            pass

    def run_turn(
        self,
        session: Session,
        user_message: str,
        settings_patch: dict[str, Any] | None = None,
        images: list[dict[str, Any]] | None = None,
    ) -> None:
        chat_images = self.messages.normalize_input_images(images or [])
        with session.lock:
            self.apply_settings_patch(session, settings_patch)
            ensure_message_backup(session)
            session.status = "running"
            session.current_goal = user_message
            if session.title == "New session":
                session.title = user_message[:48] or "Agent session"
            session.visible_messages.append({
                "role": "user",
                "content": user_message,
                "timestamp": utc_now(),
                "images": self.messages.visible_images(chat_images),
            })
            if not session.messages:
                session.messages = [
                    {"role": "system", "content": system_prompt(self.config, session)},
                    operating_instructions_message(self.config),
                    tool_operations_message(self.config),
                ]
                session.message_backup = [clone_message(message) for message in session.messages]
                invalidate_response_chain(session, "new active prompt initialized")
            else:
                system_message = {"role": "system", "content": system_prompt(self.config, session)}
                if session.messages[0] != system_message:
                    session.messages[0] = system_message
                    invalidate_response_chain(session, "system prompt changed")
                ensure_core_instruction_messages(self.config, session)
            user_record: dict[str, Any] = {
                "role": "user",
                "content": self.messages.user_content(user_message, chat_images),
            }
            if chat_images:
                user_record["images"] = chat_images
            append_conversation_message(session, user_record)
            session.add_event("user", "User message received", chars=len(user_message))
            self.context.prune_context(session)
            self.save_session(session)

        try:
            self.run_agent_loop(session)
        except Exception as exc:
            with session.lock:
                session.status = "error"
                session.recent_errors.append(str(exc))
                session.add_event("error", "Agent run failed", error=str(exc))
                session.visible_messages.append({"role": "assistant", "content": f"Agent error: {exc}", "timestamp": utc_now()})
                self.context.refresh_metrics(session)
                self.save_session(session)

    def run_agent_loop(self, session: Session) -> None:
        last_tool_observation: ToolObservation | None = None
        empty_after_tool_retries = 0
        pending_write_file_compressions: list[dict[str, Any]] = []

        for step in range(1, session.settings.max_steps + 1):
            with session.lock:
                session.add_event("llm", f"Calling LM Studio, step {step}", model=session.settings.model)
                messages = list(session.messages)
                settings = session.settings
                enabled_tools = list(session.settings.enabled_tools)
                previous_response_id, response_anchor_message_count = response_chain_for_call(session, settings)

            raw = self.llm.chat(
                messages,
                settings,
                self.tools.definitions(enabled_tools),
                previous_response_id=previous_response_id,
                response_anchor_message_count=response_anchor_message_count,
            )
            assistant_index, tool_calls, visible_content = self.record_assistant_response(session, raw)

            if not tool_calls:
                handled = self.finish_without_tool_calls(
                    session=session,
                    visible_content=visible_content,
                    last_tool_observation=last_tool_observation,
                    empty_after_tool_retries=empty_after_tool_retries,
                    step=step,
                    assistant_index=assistant_index,
                    pending_write_file_compressions=pending_write_file_compressions,
                )
                if handled == "retry":
                    empty_after_tool_retries += 1
                    continue
                return

            self.flush_pending_write_file_compressions(session, pending_write_file_compressions)

            seen_tool_signatures: set[tuple[str, str]] = set()
            for raw_call in tool_calls:
                parsed_call = self.dispatcher.parse(raw_call)
                signature = (
                    parsed_call.name,
                    json.dumps(parsed_call.args, sort_keys=True, ensure_ascii=False, default=str),
                )
                if signature in seen_tool_signatures:
                    with session.lock:
                        session.add_event(
                            "tool_call",
                            "Skipped duplicate model tool call to avoid repeating the same action",
                            tool=parsed_call.name,
                            args=parsed_call.args,
                            reason="The model emitted the same tool name and arguments more than once in one LLM step.",
                        )
                        self.save_session(session)
                    continue
                seen_tool_signatures.add(signature)

                with session.lock:
                    session.add_event("tool_call", "Dispatching model tool call")
                    self.save_session(session)

                execution = self.dispatcher.dispatch_parsed(parsed_call, enabled_tools)
                observation = self.tool_results.build_observation(execution)
                last_tool_observation = observation
                empty_after_tool_retries = 0
                self.record_tool_observation(
                    session=session,
                    observation=observation,
                    assistant_index=assistant_index,
                    pending_write_file_compressions=pending_write_file_compressions,
                )

        self.flush_pending_write_file_compressions(session, pending_write_file_compressions)

        with session.lock:
            session.status = "idle"
            session.add_event("stopped", "Stopped after max agent steps", max_steps=session.settings.max_steps)
            session.visible_messages.append({
                "role": "assistant",
                "content": f"Stopped after {session.settings.max_steps} agent steps. Increase max steps if you want me to continue.",
                "timestamp": utc_now(),
            })
            self.context.refresh_metrics(session)
            self.save_session(session)

    def record_assistant_response(self, session: Session, raw: dict[str, Any]) -> tuple[int, list[dict[str, Any]], str]:
        choice = self.messages.first_choice(raw)
        finish_reason = str(choice.get("finish_reason") or "")
        assistant_message = choice.get("message", {}) if isinstance(choice.get("message"), dict) else {}
        normalized = self.messages.normalize_assistant_message(assistant_message)
        if raw.get("usage"):
            normalized["usage"] = raw["usage"]
        content = normalized.get("content", "") or ""
        content, final_answer_requested = self.messages.strip_final_answer_marker(str(content))
        normalized["content"] = content
        thinking_content = str(normalized.get("thinking") or "").strip()
        tool_calls = normalized.get("tool_calls", [])
        visible_content = str(content).strip()

        with session.lock:
            assistant_index = len(session.messages)
            append_conversation_message(session, normalized)
            update_response_chain_after_llm(session, raw)
            consumed_images = consume_tool_images_after_llm(session)
            if visible_content:
                session.visible_messages.append({
                    "role": "assistant",
                    "content": visible_content,
                    "timestamp": utc_now(),
                    "thinking_hidden": bool(thinking_content),
                })
            if thinking_content:
                session.add_event("thinking", "Model produced <think> content", chars=len(thinking_content))
            if consumed_images:
                session.add_event(
                    "vision",
                    "Consumed one-shot tool image attachments after LLM pass",
                    image_messages=consumed_images,
                )
            session.add_event(
                "llm_response",
                "LM Studio response received",
                finish_reason=finish_reason,
                visible_chars=len(visible_content),
                thinking_chars=len(thinking_content),
                tool_calls=len(tool_calls),
                final_answer_requested=final_answer_requested,
                response_id=(raw.get("_thursday") or {}).get("response_id", ""),
                used_previous_response_id=(raw.get("_thursday") or {}).get("used_previous_response_id", False),
                cached_tokens=(raw.get("_thursday") or {}).get("cached_tokens", 0),
                request_input_items=(raw.get("_thursday") or {}).get("request_input_items", 0),
                request_tool_count=(raw.get("_thursday") or {}).get("request_tool_count", 0),
                request_has_previous_response_id=(raw.get("_thursday") or {}).get("request_has_previous_response_id", False),
            )
            self.save_session(session)

        return assistant_index, tool_calls, visible_content

    def finish_without_tool_calls(
        self,
        session: Session,
        visible_content: str,
        last_tool_observation: ToolObservation | None,
        empty_after_tool_retries: int,
        step: int,
        assistant_index: int,
        pending_write_file_compressions: list[dict[str, Any]],
    ) -> str:
        if (
            visible_content
            and last_tool_observation is not None
            and last_tool_observation.llm_images
            and self.looks_like_visual_refusal(visible_content)
        ):
            if empty_after_tool_retries < 2 and step < session.settings.max_steps:
                with session.lock:
                    self.remove_assistant_turn(session, assistant_index, visible_content)
                    invalidate_response_chain(session, "removed visual refusal before screenshot retry")
                    append_conversation_message(session, self.visual_retry_instruction(last_tool_observation))
                    session.add_event(
                        "retry",
                        "Model answered as if it could not see an attached tool screenshot; reattached image and retrying",
                        tool=last_tool_observation.name,
                        image_count=len(last_tool_observation.llm_images),
                    )
                    self.save_session(session)
                return "retry"

        if not visible_content and last_tool_observation is not None:
            if empty_after_tool_retries < 1 and step < session.settings.max_steps:
                with session.lock:
                    if assistant_index < len(session.messages):
                        session.messages.pop(assistant_index)
                    invalidate_response_chain(session, "removed empty assistant response before retry")
                    append_conversation_message(session, self.tool_continuation_instruction(last_tool_observation))
                    session.add_event(
                        "retry",
                        "Model returned empty text after tool result; added continuation instruction and retrying",
                        tool=last_tool_observation.name,
                    )
                    self.save_session(session)
                return "retry"
            self.flush_pending_write_file_compressions(session, pending_write_file_compressions)
            self.append_generated_fallback(
                session,
                self.tool_results.fallback_tool_response(last_tool_observation),
                event="Model returned empty text after tool result; generated a tool-result summary",
                tool=last_tool_observation.name,
            )
        elif not visible_content:
            self.append_generated_fallback(
                session,
                self.tool_results.fallback_empty_response(),
                event="Model returned no visible assistant content",
            )
        else:
            self.flush_pending_write_file_compressions(session, pending_write_file_compressions)

        with session.lock:
            session.status = "idle"
            session.add_event("done", "Agent turn completed")
            self.context.refresh_metrics(session)
            self.save_session(session)
        return "done"

    def record_tool_observation(
        self,
        session: Session,
        observation: ToolObservation,
        assistant_index: int,
        pending_write_file_compressions: list[dict[str, Any]],
    ) -> None:
        with session.lock:
            session.tool_counts[observation.name] = session.tool_counts.get(observation.name, 0) + 1
            session.add_event("tool_call", f"Invoked {observation.name}", tool=observation.name, args=observation.args)
            if observation.modified_path and observation.modified_path not in session.modified_files:
                session.modified_files.append(observation.modified_path)
            if observation.error_text:
                session.recent_errors.append(clamp_text(observation.error_text, session.settings.context_window))

            append_conversation_message(session, self.tool_results.tool_message(observation))
            loaded_skill = append_loaded_skill_message(
                session,
                observation.name,
                observation.ok,
                observation.args,
                self.tool_results.output(observation.stored_result),
            )
            if loaded_skill:
                session.add_event(
                    "skills",
                    "Loaded skill into conversation timeline",
                    skill=observation.args.get("skill_name"),
                )
            if observation.llm_images:
                session.add_event(
                    "vision",
                    f"Attached {len(observation.llm_images)} image(s) to {observation.name} tool result",
                    tool=observation.name,
                    image_count=len(observation.llm_images),
                )
            session.add_event(
                "tool_result",
                f"{observation.name} returned {'success' if observation.ok else 'error'}",
                tool=observation.name,
                ok=observation.ok,
                result=self.tool_results.compact_stored_result(observation.name, observation.stored_result),
            )

            compression_payload = self.tool_results.write_file_compression_payload(
                observation,
                self.context.should_summarize_written_content,
            )
            if compression_payload:
                pending_write_file_compressions.append({
                    "assistant_index": assistant_index,
                    "settings": session.settings,
                    **compression_payload,
                })
            else:
                self.context.prune_context(session)
            self.save_session(session)

    def flush_pending_write_file_compressions(
        self,
        session: Session,
        pending_write_file_compressions: list[dict[str, Any]],
    ) -> None:
        if not pending_write_file_compressions:
            return
        with session.lock:
            for pending in pending_write_file_compressions:
                self.context.compress_successful_write_file_call_after_observation(session=session, **pending)
            pending_write_file_compressions.clear()
            self.context.prune_context(session)
            self.save_session(session)

    def append_generated_fallback(self, session: Session, content: str, event: str, **event_data: Any) -> None:
        with session.lock:
            session.messages.append({"role": "assistant", "content": content})
            session.message_backup.append(clone_message({"role": "assistant", "content": content}))
            session.visible_messages.append({
                "role": "assistant",
                "content": content,
                "timestamp": utc_now(),
                "generated_fallback": True,
            })
            session.add_event("fallback", event, **event_data)

    def tool_continuation_instruction(self, observation: ToolObservation) -> dict[str, str]:
        return {
            "role": "user",
            "content": (
                "[Thursday Continuation Instruction]\n\n"
                f"The previous assistant message was empty after `{observation.name}` returned. "
                "Continue the original user request now using the tool result above. "
                "If the result included image attachments, inspect those images directly as visual input. "
                "If that result is only partial evidence, call the next useful tool. "
                "If it is enough to answer, answer directly. Do not return an empty message."
            ),
        }

    def visual_retry_instruction(self, observation: ToolObservation) -> dict[str, Any]:
        return {
            "role": "user",
            "content": (
                "[Thursday Visual Retry]\n\n"
                f"The previous `{observation.name}` result included screenshot image attachments. "
                "The same image(s) are attached again to this message. Inspect them directly as visual input now. "
                "Answer the user's original request from what you can actually see. "
                "Do not say you only have file paths, and do not say you cannot inspect screenshots. "
                "If any detail is unclear, describe the visible state first and then name the uncertainty."
            ),
            "images": [clone_message(image) for image in observation.llm_images],
        }

    def looks_like_visual_refusal(self, content: str) -> bool:
        lowered = content.lower()
        return any(phrase in lowered for phrase in VISUAL_REFUSAL_PHRASES)

    def remove_assistant_turn(self, session: Session, assistant_index: int, visible_content: str) -> None:
        if assistant_index < len(session.messages):
            session.messages.pop(assistant_index)
        if session.message_backup:
            last_backup = session.message_backup[-1]
            if last_backup.get("role") == "assistant" and str(last_backup.get("content") or "").strip() == visible_content:
                session.message_backup.pop()
        if session.visible_messages:
            last_visible = session.visible_messages[-1]
            if last_visible.get("role") == "assistant" and str(last_visible.get("content") or "").strip() == visible_content:
                session.visible_messages.pop()

    def apply_settings_patch(self, session: Session, settings_patch: dict[str, Any] | None) -> None:
        if not settings_patch:
            return
        for key, value in settings_patch.items():
            if hasattr(session.settings, key):
                if key in {"model", "endpoint"} and getattr(session.settings, key) != value:
                    invalidate_response_chain(session, f"{key} changed")
                setattr(session.settings, key, value)
















