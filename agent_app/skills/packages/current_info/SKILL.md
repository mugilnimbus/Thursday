---
name: current_info
what_it_does: Teaches how to answer live, recent, changing, or time-sensitive questions with tool-backed evidence.
when_to_use: The task involves weather, news, prices, current docs, APIs, schedules, sports, releases, product details, laws, latest/current/today/now, relative dates, local context, or anything likely to have changed.
description: What it does: Teaches how to answer live, recent, changing, or time-sensitive questions with tool-backed evidence. When to use: The task involves weather, news, prices, current docs, APIs, schedules, sports, releases, product details, laws, latest/current/today/now, relative dates, local context, or anything likely to have changed.
---

# Skill: current_info

## Purpose
Use this skill to answer questions involving live, current, changing, or time-sensitive information.

## Operating Model
Current location, local date, local time, timezone, weekday, and UTC timestamp are not injected automatically. Call `get_current_datetime_location` when the task depends on now, today, tomorrow, here, local weather, reminders, schedules, or relative dates.

Current information requires tool-backed evidence. Search results are leads, not the answer.

## Tool Strategy
- Use `get_current_datetime_location` first when relative time, local context, or configured location matters.
- Use `web_search` to find likely reliable sources.
- If `web_search` only returns links, continue with `run_command` using PowerShell HTTP commands such as `Invoke-RestMethod` or `Invoke-WebRequest`.
- For weather-specific tasks, load the `weather` skill.
- For structured JSON/text API fetching, load the `api_fetching` skill when you need the detailed command pattern.
- Prefer official or primary sources when available.
- Use one alternate reliable source if the first source is inaccessible or unclear.
- Do not use `capture_webpage` for weather, news, prices, current facts, or search-result text. Screenshots are visual evidence, not reliable structured source data for current-information answers.

## Workflow
1. Resolve location and date/time from the user message or `get_current_datetime_location`.
2. Search for likely sources if the source is not already known.
3. Fetch the source/API/page content using available general tools.
4. Extract the actual value, condition, date, rule, price, schedule, or answer.
5. Compare another source when accuracy matters or the source is ambiguous.
6. Answer directly and name the source.

## Common Failures
- Returning a list of links instead of the answer.
- Searching repeatedly with new query wording instead of fetching a source.
- Capturing search-result screenshots instead of fetching a text/API source.
- Using stale model memory for live facts.
- Ignoring the configured current location when the user asks “outside”, “here”, or “now”.

## Do Not
- Do not tell the user to check a website themselves.
- Do not present search-result titles as facts.
- Do not guess current facts from memory.
- Do not call visual/screenshot tools unless the user explicitly asks for a visual check.

## Final Answer
Give the current answer directly, include the location/date/time if relevant, and mention the source name briefly.
