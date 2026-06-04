---
name: image_input
what_it_does: Teaches how to use uploaded images, file-path image attachments, and tool screenshots as visual evidence without embedding image bytes in text.
when_to_use: The user attaches images, a tool returns llm_images, or the task asks to inspect, describe, compare, debug, or reason from visual input.
description: What it does: Teaches how to use uploaded images, file-path image attachments, and tool screenshots as visual evidence without embedding image bytes in text. When to use: The user attaches images, a tool returns llm_images, or the task asks to inspect, describe, compare, debug, or reason from visual input.
---

# Skill: image_input

## Purpose
Use this skill when the user attaches images or when tools produce screenshots for VLM inspection.

## Operating Model
Images are passed by file path and attached to the LLM request. Do not paste base64 or raw image bytes into conversation text.

## Tool Strategy
- Use the attached image directly when it is already present.
- Use `capture_webpage` to create screenshots for webpages or HTML files.
- Use file paths and image metadata; avoid embedding complete image data in text.

## Workflow
1. Identify which image(s) are relevant.
2. Use the image as visual evidence.
3. If more visual evidence is needed, capture or request it through tools.
4. Combine image evidence with code/file/tool evidence when needed.

## Common Failures
- Treating screenshots as text-only evidence.
- Re-sending base64 in the next prompt.
- Ignoring image paths attached by tool results.

## Do Not
- Do not paste full image data into context.
- Do not perform DOM text extraction as a substitute for image inspection.

## Final Answer
Answer from the visual evidence and mention if the image was insufficient or unclear.
