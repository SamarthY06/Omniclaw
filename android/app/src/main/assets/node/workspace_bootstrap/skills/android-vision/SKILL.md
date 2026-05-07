---
name: android-vision
description: On-device OCR + OpenAI computer-tool grounding for Android screenshots.
exec_pattern: "node /data/user/0/com.ben/files/openclaw/tools/android_vision.js *"
sensitivity: S0
---

# Android Vision skill

Three subcommands:

1. `text-locate --image PATH --target STR [--screen-width W --screen-height H]`
   Free, on-device. Uses ML Kit Text Recognition. Returns image-pixel coords
   AND screen coords (if W/H provided). Score < 0.55 = miss; escalate.

2. `locate --image PATH --target STR [--screen-width W --screen-height H]`
   OpenAI Responses API GA `computer` tool, gpt-5.5. Two-turn handshake;
   returns a single click action. Use as fallback when text-locate misses.
   S2 (image leaves device).

3. `read --image PATH --question STR`
   Multimodal chat-completions, gpt-5.5. For "what does this screenshot
   say" / "summarize the visible messages". S2.

## Cascade

text_locate -> vision_locate -> ask user. NEVER browser fallback.
