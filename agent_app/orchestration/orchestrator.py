from __future__ import annotations

import copy
import json
from typing import Any, Callable

from ..config import AppConfig
from ..llm import LMStudioChatClient
from ..models import Session
from ..prompts import render_system_prompt
from ..reminders import ReminderStore
from ..skills import SkillCatalog
from ..tools import ToolRegistry
from ..tools.dispatcher import ToolCallDispatcher
from ..tools.results import ToolObservation, ToolResultPresenter
from ..utils import clamp_text, utc_now
from .context_manager import ConversationContextManager
from .messages import ConversationMessageBuilder


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
            self.ensure_message_backup(session)
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
                    {"role": "system", "content": self.system_prompt(session)},
                    self.operating_instructions_message(),
                    self.always_active_tool_operations_message(),
                ]
                session.message_backup = [self.clone_message(message) for message in session.messages]
                self.invalidate_response_chain(session, "new active prompt initialized")
            else:
                system_message = {"role": "system", "content": self.system_prompt(session)}
                if session.messages[0] != system_message:
                    session.messages[0] = system_message
                    self.invalidate_response_chain(session, "system prompt changed")
                self.ensure_core_instruction_messages(session)
            user_record: dict[str, Any] = {
                "role": "user",
                "content": self.messages.user_content(user_message, chat_images),
            }
            if chat_images:
                user_record["images"] = chat_images
            self.append_conversation_message(session, user_record)
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
                previous_response_id, response_anchor_message_count = self.response_chain_for_call(session, settings)

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
                            "Skipped duplicate model tool call in the same step",
                            tool=parsed_call.name,
                            args=parsed_call.args,
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
            self.append_conversation_message(session, normalized)
            self.update_response_chain_after_llm(session, raw)
            consumed_images = self.consume_tool_images_after_llm(session)
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
        if not visible_content and last_tool_observation is not None:
            if empty_after_tool_retries < 1 and step < session.settings.max_steps:
                with session.lock:
                    if assistant_index < len(session.messages):
                        session.messages.pop(assistant_index)
                    self.invalidate_response_chain(session, "removed empty assistant response before retry")
                    self.append_conversation_message(session, self.tool_continuation_instruction(last_tool_observation))
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

            self.append_conversation_message(session, self.tool_results.tool_message(observation))
            loaded_skill_message = self.loaded_skill_message(session, observation)
            if loaded_skill_message:
                self.append_conversation_message(session, loaded_skill_message)
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
            session.message_backup.append(self.clone_message({"role": "assistant", "content": content}))
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
                "If that result is only partial evidence, call the next useful tool. "
                "If it is enough to answer, answer directly. Do not return an empty message."
            ),
        }

    def apply_settings_patch(self, session: Session, settings_patch: dict[str, Any] | None) -> None:
        if not settings_patch:
            return
        for key, value in settings_patch.items():
            if hasattr(session.settings, key):
                if key in {"model", "endpoint"} and getattr(session.settings, key) != value:
                    self.invalidate_response_chain(session, f"{key} changed")
                setattr(session.settings, key, value)

    def system_prompt(self, session: Session) -> str:
        return render_system_prompt(self.config)

    def operating_instructions_message(self) -> dict[str, str]:
        return {
            "role": "user",
            "content": (
                "[Thursday Operating Instructions]\n\n"
                "This is a permanent operating instruction message, not a new task request.\n\n"
                "A skill is a task operating package with `SKILL.md` metadata, concise instructions, and optional "
                "`references/`, `scripts/`, or `assets/` resources. Skill metadata is visible in the catalog; "
                "full skill bodies are loaded only through `load_skill`; bundled resources are loaded only through "
                "`read_skill_resource` when a loaded skill points to a specific needed file.\n\n"
                "Before performing a task, check whether one of the available skills in the always-active "
                "`tool_operations` message and `[Thursday Skill Catalog]` would help. If a useful skill is not already present in the "
                "conversation, call `load_skill` with that skill name and then use the loaded user message "
                "as task instruction before continuing.\n\n"
                "Do not treat loaded skill messages as user requests. They are the user's instructions for "
                "how you should perform the current or future task."
            ),
        }

    def always_active_tool_operations_message(self) -> dict[str, str]:
        catalog = SkillCatalog(self.config.prompt_dir / "skills")
        spec = catalog.get("tool_operations")
        content = spec.body if spec else "The always-active tool operations skill is missing."
        return {
            "role": "user",
            "content": (
                "[Thursday Always Active Skill: tool_operations]\n\n"
                "The user is teaching this skill permanently so you know how Thursday tools and skills work. "
                "Treat it as operating instruction, not as a user task.\n\n"
                f"{content}\n\n"
                f"{catalog.catalog_markdown()}"
            ),
        }

    def ensure_core_instruction_messages(self, session: Session) -> None:
        changed = 0
        changed += self.upsert_instruction_message(
            session,
            marker="[Thursday Operating Instructions]",
            message=self.operating_instructions_message(),
            insert_at=1,
        )
        changed += self.upsert_instruction_message(
            session,
            marker="[Thursday Always Active Skill: tool_operations]",
            message=self.always_active_tool_operations_message(),
            insert_at=2,
        )
        if changed:
            self.invalidate_response_chain(session, "core operating instruction messages changed")
            session.add_event("skills", "Refreshed permanent operating instruction messages", count=changed)

    def has_instruction_message(self, session: Session, marker: str) -> bool:
        return any(message.get("role") == "user" and marker in str(message.get("content") or "") for message in session.messages)

    def upsert_instruction_message(self, session: Session, marker: str, message: dict[str, str], insert_at: int) -> int:
        for index, existing in enumerate(session.messages):
            if existing.get("role") == "user" and marker in str(existing.get("content") or ""):
                if existing.get("content") != message["content"]:
                    session.messages[index] = message
                    return 1
                return 0
        session.messages.insert(min(insert_at, len(session.messages)), message)
        return 1

    def loaded_skill_message(self, session: Session, observation: ToolObservation) -> dict[str, str] | None:
        if observation.name != "load_skill" or not observation.ok:
            return None
        output = self.tool_results.output(observation.stored_result)
        content = output.get("instruction_message")
        if not isinstance(content, str) or not content.strip():
            return None
        skill_name = str(output.get("skill_name") or observation.args.get("skill_name") or "").strip()
        marker = f"[Thursday Loaded Skill: {skill_name}]"
        if skill_name and any(
            message.get("role") == "user" and marker in str(message.get("content") or "")
            for message in session.messages
        ):
            return None
        return {"role": "user", "content": content}

    def ensure_message_backup(self, session: Session) -> None:
        if not session.message_backup and session.messages:
            session.message_backup = [self.clone_message(message) for message in session.messages]

    def append_conversation_message(self, session: Session, message: dict[str, Any], backup: bool = True) -> None:
        cloned = self.clone_message(message)
        session.messages.append(cloned)
        if backup:
            session.message_backup.append(self.clone_message(message))

    def clone_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(message)

    def consume_tool_images_after_llm(self, session: Session) -> int:
        consumed = 0
        for message in session.messages:
            if message.get("role") != "tool" or not message.get("images"):
                continue
            message["llm_images_consumed"] = True
            message.pop("images", None)
            consumed += 1
        return consumed

    def invalidate_response_chain(self, session: Session, reason: str) -> None:
        session.response_chain_valid = False
        session.last_response_id = ""
        session.response_anchor_message_count = 0
        session.response_chain_invalid_reason = reason

    def response_chain_for_call(self, session: Session, settings: Any) -> tuple[str, int]:
        endpoint = str(settings.endpoint or "").rstrip("/")
        if not session.response_chain_valid or not session.last_response_id:
            return "", 0
        if session.response_chain_model != settings.model:
            self.invalidate_response_chain(session, "model changed since previous response")
            return "", 0
        if session.response_chain_endpoint.rstrip("/") != endpoint:
            self.invalidate_response_chain(session, "endpoint changed since previous response")
            return "", 0
        if session.response_anchor_message_count < 1 or session.response_anchor_message_count > len(session.messages):
            self.invalidate_response_chain(session, "response anchor was outside active message range")
            return "", 0
        return session.last_response_id, session.response_anchor_message_count

    def update_response_chain_after_llm(self, session: Session, raw: dict[str, Any]) -> None:
        meta = raw.get("_thursday") if isinstance(raw.get("_thursday"), dict) else {}
        response_id = str(meta.get("response_id") or raw.get("id") or "")
        if not response_id or meta.get("api") != "responses":
            self.invalidate_response_chain(session, "LLM call did not return a Responses API id")
            return
        previous_failed = meta.get("previous_response_failed")
        session.last_response_id = response_id
        session.response_chain_valid = True
        session.response_anchor_message_count = len(session.messages)
        session.response_chain_model = session.settings.model
        session.response_chain_endpoint = session.settings.endpoint.rstrip("/")
        session.response_chain_invalid_reason = ""
        if previous_failed:
            session.add_event(
                "llm",
                "Previous response id was unavailable; rebuilt response chain from active transcript",
                previous_response_id=previous_failed.get("id"),
                error=previous_failed.get("error"),
                new_response_id=response_id,
            )
