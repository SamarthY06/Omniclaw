---
name: voice
description: OpenAI Realtime over WSS for STT/LLM/TTS, after the wake word.
sensitivity: S2
---

# Voice skill

Voice is handled in Kotlin (BenVoiceService.kt) and Realtime API. The agent
itself doesn't directly call this - it's the substrate the agent runs on.

Lifecycle:
- BenWakewordService matches "Ben" -> starts BenVoiceService.
- BenVoiceService opens WSS to `wss://api.openai.com/v1/realtime?model=gpt-realtime`.
- Mic audio (24 kHz PCM16) streams up; audio.delta plays back through AudioTrack.
- 180s of silence ends the session; soft chime plays; wake listener re-arms.
