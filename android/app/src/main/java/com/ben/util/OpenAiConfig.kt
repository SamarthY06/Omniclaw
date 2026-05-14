package com.ben.util

/**
 * Single source of truth for every OpenAI-related constant we ship to the
 * model. Pre-fix these values were scattered across BenVoiceService,
 * builtin_tools.js, android_vision.js, and a handful of other files; making
 * a coordinated change ("upgrade to gpt-realtime-2026", "rename voice from
 * marin to coral", "tighten max_tokens") meant chasing them down across the
 * codebase and inevitably leaving one stale.
 *
 * Kotlin code MUST read from this object. The Node side has its own copy in
 * src/openclaw/builtin_tools.js (vision fallback chain) and
 * src/tools/android_vision.js - keeping them in sync is documented as
 * MIGRATION_TODO #6 (centralised model config). Until then, any change here
 * needs a parallel edit on those JS files; do a grep for the model name and
 * verify before merging.
 */
object OpenAiConfig {
    // ---- Realtime voice loop --------------------------------------------

    /**
     * Endpoint host for the Realtime WSS. Centralised so a future
     * Azure-OpenAI / proxy migration only edits one line.
     */
    const val REALTIME_WSS_HOST = "wss://api.openai.com"

    /**
     * Realtime model. As of 2026-05 this is the GA path; the previous
     * gpt-4o-realtime-preview path requires the older OpenAI-Beta header
     * value. We deliberately default to the GA name + GA header.
     */
    const val REALTIME_MODEL = "gpt-realtime"
    const val REALTIME_BETA_HEADER = "realtime=v1"

    /** Voice character. Other valid values: alloy, echo, fable, marin, sage, shimmer, verse, coral. */
    const val REALTIME_VOICE = "marin"

    /** Speech-to-text model used for the transcription side of the Realtime stream. */
    const val REALTIME_TRANSCRIPTION_MODEL = "whisper-1"

    /** Locked-in input/output language. */
    const val REALTIME_LANGUAGE = "en"

    /** PCM-16 mono 24 kHz - both directions. */
    const val REALTIME_AUDIO_FORMAT = "pcm16"
    const val REALTIME_SAMPLE_RATE_HZ = 24_000

    /** Hard ceiling on a single response's audio token count (~30-40 s of speech). */
    const val REALTIME_MAX_RESPONSE_OUTPUT_TOKENS = 800

    // ---- Server VAD -----------------------------------------------------
    //
    // v0.1.6: a real-device run on Samsung One UI 6 with the speaker active
    // showed the model spontaneously emitting "I can help you with that"
    // type filler at session start, before the user had said anything.
    // Root cause: VAD_THRESHOLD=0.6 was sensitive enough to misclassify
    // ambient noise / room reverb as user speech, which triggered the
    // server-side create_response flow. Bumping to 0.8 and lengthening the
    // prefix-padding makes the recognizer demand a more confident speech
    // signal before declaring "user spoke", which kills the false-positive
    // greeting without hurting wake-after-talk responsiveness.

    const val VAD_THRESHOLD = 0.8
    const val VAD_PREFIX_PADDING_MS = 500
    const val VAD_SILENCE_DURATION_MS = 1_000

    // ---- Session lifecycle timers --------------------------------------

    /** Silence after a response.done before we close the conversation. */
    const val POST_RESPONSE_SILENCE_MS = 180_000L

    /** Hard cap on total conversation length regardless of activity. */
    const val HARD_SESSION_CAP_MS = 600_000L

    /** Per-tool roundtrip cap (Node bridge -> tool -> Node bridge). */
    const val TOOL_INVOKE_TIMEOUT_MS = 15_000

    /**
     * v0.1.9: how long the mic stays muted AFTER `response.done` to cover
     * the AudioTrack drain tail. The model marks the response complete
     * server-side (response.done fires) but we keep playing the queued
     * audio.delta bytes through AudioTrack for a few hundred ms more -
     * during that window any speaker bleed gets re-captured by the mic
     * and whisper transcribes it as new "user" input, which drove the
     * 0.1.5-0.1.8 echo-loop bug ("Opening WhatsApp" -> whisper hears
     * "Binning WhatsApp" -> model responds again, ad infinitum).
     *
     * The primary mute path is now AudioTrack-drain-aware (poll
     * playbackHeadPosition vs bytesWritten); this is the fallback for
     * devices where that returns 0 / negative or where AudioTrack is null.
     */
    const val MIC_MUTE_TAIL_MS = 1_200L

    // ---- Reconnect-with-backoff (WSS failures) -------------------------

    /**
     * On a WSS open / read failure during a session, retry up to 3 times
     * with exponential backoff before giving up and ending the session.
     * Pre-fix, a single transient handshake failure ended the session
     * permanently and the user had to say the wake word again.
     */
    const val RECONNECT_MAX_ATTEMPTS = 3
    val RECONNECT_BACKOFFS_MS = longArrayOf(500L, 1_000L, 2_000L)

    // ---- Tool call timeouts --------------------------------------------

    /** NodeBridgeService inbound RPC port. */
    const val NODE_BRIDGE_PORT = 18_792

    // ---- Vision (multimodal) -------------------------------------------
    // Mirrors the fallback chain in builtin_tools.js. Kotlin side
    // currently doesn't call vision directly (only via the Node bridge),
    // but constants are kept here so the centralisation is one-stop.

    val VISION_MODEL_FALLBACK_CHAIN = listOf("gpt-5.5", "gpt-4o", "gpt-4o-mini")

    // ---- Cost / safety ledger ------------------------------------------

    /**
     * USD price-per-million-input-tokens and per-million-output-tokens for
     * each accounting bucket we track. Best-effort estimates; refresh if
     * OpenAI pricing changes (no API to fetch this dynamically).
     */
    val COST_PRICES_USD: Map<String, CostPrice> = mapOf(
        "realtime_audio" to CostPrice(inputPerMillion = 100.0, outputPerMillion = 200.0),
        "realtime_text" to CostPrice(inputPerMillion = 5.0, outputPerMillion = 20.0),
        "vision" to CostPrice(inputPerMillion = 10.0, outputPerMillion = 30.0),
        "chat" to CostPrice(inputPerMillion = 2.5, outputPerMillion = 10.0),
    )

    /**
     * Default soft caps (in USD). Overridable via BenSecrets cost_cap_*
     * keys so the user can tune them without rebuilding. When the daily
     * cap is hit we don't permanently brick the assistant - we surface a
     * single refusal message and the user can raise the cap in settings.
     */
    const val DEFAULT_DAILY_CAP_USD = 5.0
    const val DEFAULT_MONTHLY_CAP_USD = 100.0
}

/**
 * Pair of input/output prices for one accounting bucket.
 * Stored as USD per 1,000,000 tokens to match OpenAI's pricing page.
 */
data class CostPrice(
    val inputPerMillion: Double,
    val outputPerMillion: Double,
) {
    fun cost(inputTokens: Long, outputTokens: Long): Double {
        return (inputTokens / 1_000_000.0) * inputPerMillion +
            (outputTokens / 1_000_000.0) * outputPerMillion
    }
}

/**
 * Discriminator for what kind of OpenAI call a recorded usage event came
 * from. Used by CostLedger.record() so the per-bucket aggregation knows
 * which COST_PRICES_USD row to apply.
 */
enum class CallKind(val accountingBucket: String) {
    /** Realtime WSS audio in/out (mic upload + speaker playback tokens). */
    REALTIME_AUDIO("realtime_audio"),
    /** Realtime conversation text tokens (transcripts, system prompt, tool I/O). */
    REALTIME_TEXT("realtime_text"),
    /** Multimodal vision.read_screen calls. */
    VISION("vision"),
    /** Plain chat-completions (currently unused; reserved for future). */
    CHAT("chat"),
}
