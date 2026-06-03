---
name: reminders
what_it_does: Teaches how to create, inspect, update, delete, and reason about scheduled reminders or future agent jobs.
when_to_use: The user asks to remind, schedule, repeat, monitor, follow up, wake up, run later, or manage existing reminders.
description: What it does: Teaches how to create, inspect, update, delete, and reason about scheduled reminders or future agent jobs. When to use: The user asks to remind, schedule, repeat, monitor, follow up, wake up, run later, or manage existing reminders.
---

# Skill: reminders

## Purpose
Use this skill for reminders, scheduled tasks, recurring jobs, wakeups, and future follow-ups.

## Operating Model
Reminders are future Thursday agent turns, not simple notifications. When a reminder is due, Thursday should use tools as needed and complete the reminder task.

## Tool Strategy
- Use `get_current_datetime_location` before converting relative times such as today, tomorrow, tonight, or next week.
- Use `create_reminder` to create reminders.
- Use `list_reminders` before updating or deleting when the id is unknown.
- Use `update_reminder` to change schedule, prompt, title, or enabled state.
- Use `delete_reminder` to remove reminders.

## Workflow
1. Convert relative time into exact local date/time using `get_current_datetime_location`.
2. Choose the correct schedule type: once, every_minutes, hourly, daily, weekly, or monthly.
3. Write the reminder prompt as the actual future task Thursday should perform.
4. Confirm the schedule clearly after tool success.

## Common Failures
- Creating a vague reminder prompt that cannot run later.
- Asking for timezone when the system timezone is already configured.
- Treating recurring reminders as one-time tasks.

## Do Not
- Do not create reminders in the past.
- Do not pass a timezone field unless the tool schema explicitly asks for one.
- Do not delete or update without an id unless you listed reminders first.

## Final Answer
State what reminder was created/updated/deleted and when it will run.
