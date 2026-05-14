package com.ben.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.util.Log
import java.util.Locale
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.ben.util.BenSecrets
import com.ben.wake.WakePhraseMatcher
import java.util.concurrent.ConcurrentLinkedDeque

/**
 * Always-on wake word listener. Uses Android's SpeechRecognizer in
 * partial-results mode and runs an auto-restart loop because the system
 * recognizer stops on every silence boundary.
 *
 * On a partial transcript that fuzzy-matches the user's configured phrase, we
 * cancel the recognizer and hand off to BenVoiceService (OpenAI Realtime).
 *
 * v0.1.3: every recognition event (READY / BEGIN / PARTIAL / RESULT / ERROR /
 * RESTART / WAKE_MATCH) is appended to an in-memory ring buffer AND broadcast
 * via LocalBroadcastManager. MicTestActivity subscribes to those broadcasts to
 * show the user, in real time, what the recognizer is hearing - so when "Ben"
 * does nothing the user (and we) can tell whether it's a permission issue, a
 * mic issue, an offline-pack issue, or a wake-matcher false-negative.
 *
 * v0.1.3: also drops EXTRA_PREFER_OFFLINE on the second consecutive
 * ERROR_NO_MATCH / ERROR_SPEECH_TIMEOUT / ERROR_LANGUAGE_NOT_SUPPORTED so a
 * device without an offline en-US pack can still wake.
 *
 * v0.1.6: real-device finding on a Samsung One UI 6 phone set to en-IN:
 * the recognizer was rejecting ALL requests with ERROR_LANGUAGE_UNAVAILABLE
 * (code 13) in a tight loop. Root cause was two-fold:
 *   1. The intent set EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE=true with
 *      EXTRA_LANGUAGE="en-US". The on-device recognizer on this phone only
 *      had en-IN installed; with the strict "only return preference" flag
 *      set, any locale mismatch hard-fails instead of degrading gracefully.
 *   2. The previous network-recognizer fallback flipped EXTRA_PREFER_OFFLINE
 *      off but kept asking for the same en-US locale, AND on Android 12+ we
 *      were still using `createOnDeviceSpeechRecognizer` (the network
 *      fallback never actually engaged).
 * Fix: drop the strict-preference flag, cycle through a candidate locale
 * list [en-US, en-IN, en-GB, system-default, no-locale], and force-switch
 * to the network recognizer on ERROR_LANGUAGE_UNAVAILABLE specifically
 * (instead of only after two consecutive errors).
 */
class BenWakewordService : Service() {
    private val tag = "BenWakewordService"
    private var recognizer: SpeechRecognizer? = null
    private val handler = Handler(Looper.getMainLooper())
    private var phrase: String = "Ben"
    private var paused: Boolean = false
    private var wantsRestart: Boolean = true
    private var preferOffline: Boolean = true
    private var useOnDeviceRecognizer: Boolean = true
    private var consecutiveErrors: Int = 0
    private var localeIndex: Int = 0

    /**
     * Candidate locales in priority order. We start with en-US (most likely
     * to work on a US-locale phone), then en-IN (the most common Indian
     * English variant the user is on per the real-device log), then en-GB,
     * then the device default, and finally an empty string which tells the
     * recognizer "use whatever you've got". On ERROR_LANGUAGE_UNAVAILABLE
     * we walk the list; on ERROR_NO_MATCH / ERROR_SPEECH_TIMEOUT we stay on
     * the current locale.
     *
     * Computed lazily because Locale.getDefault() needs a Context lifecycle.
     */
    private val candidateLocales: List<String> by lazy {
        val default = Locale.getDefault().toLanguageTag().ifBlank { "" }
        listOf("en-US", "en-IN", "en-GB", default, "")
            .distinct()
            .filter { it == "" || it.startsWith("en", ignoreCase = true) || it.isBlank() }
    }
    private val currentLocale: String
        get() = candidateLocales.getOrElse(localeIndex) { "" }

    // Defensive auto-resume: if BenVoiceService crashes (or its hard cap
    // fails to fire), we'd otherwise stay paused forever and never wake
    // again until process restart. Set just beyond the voice service's
    // 600s (v0.1.3) hard cap.
    private val defensiveResumeRunnable = Runnable {
        Log.w(tag, "Defensive auto-resume: no ACTION_RESUME after pause - re-arming wake listener")
        emit(EventKind.RESTART, "defensive auto-resume after pause timeout")
        paused = false
        wantsRestart = true
        consecutiveErrors = 0
        restartShortly()
    }

    override fun onCreate() {
        super.onCreate()
        // Intentionally NOT a foreground service: mic access is inherited from
        // BenForegroundService's foregroundServiceType="microphone".
        phrase = BenSecrets.wakePhrase(this)
        emit(EventKind.RESTART, "service created, phrase='$phrase'")
        startListening()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> {
                paused = true
                try { recognizer?.cancel() } catch (_: Exception) {}
                handler.removeCallbacks(defensiveResumeRunnable)
                handler.postDelayed(defensiveResumeRunnable, DEFENSIVE_RESUME_MS)
                emit(EventKind.RESTART, "paused (voice session active)")
            }
            ACTION_RESUME -> {
                handler.removeCallbacks(defensiveResumeRunnable)
                paused = false
                wantsRestart = true
                consecutiveErrors = 0
                emit(EventKind.RESTART, "resumed")
                restartShortly()
            }
            ACTION_RELOAD_PHRASE -> {
                phrase = BenSecrets.wakePhrase(this)
                emit(EventKind.RESTART, "phrase reloaded -> '$phrase'")
            }
            ACTION_FORCE_RESTART -> {
                paused = false
                wantsRestart = true
                consecutiveErrors = 0
                preferOffline = true
                useOnDeviceRecognizer = true
                localeIndex = 0
                emit(EventKind.RESTART, "manual force restart from MicTestActivity (reset locale + recognizer)")
                restartShortly()
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?) = null

    override fun onDestroy() {
        super.onDestroy()
        wantsRestart = false
        try { recognizer?.destroy() } catch (_: Exception) {}
        handler.removeCallbacksAndMessages(null)
        emit(EventKind.RESTART, "service destroyed")
    }

    private fun startListening() {
        if (paused || !wantsRestart) return
        val ctx = applicationContext
        // useOnDeviceRecognizer is flipped off when we see ERROR_LANGUAGE_UNAVAILABLE
        // so a stubborn on-device pack can be sidestepped by the cloud recognizer.
        val sr = if (useOnDeviceRecognizer &&
                     Build.VERSION.SDK_INT >= 31 &&
                     SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx)
        } else {
            SpeechRecognizer.createSpeechRecognizer(ctx)
        }
        sr.setRecognitionListener(listener)
        recognizer = sr
        val locale = currentLocale
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            if (preferOffline) {
                putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            }
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            // Suggest a locale, but DO NOT set EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE.
            // The strict-preference flag is what was making Samsung's
            // en-IN-only on-device pack hard-reject our en-US request with
            // ERROR_LANGUAGE_UNAVAILABLE in a tight loop. Letting the
            // recognizer fall back to whatever locale it has installed is the
            // forgiving behaviour we want for wake-word detection - we don't
            // care about transcription quality at this stage, just whether
            // the user said something close to "Ben".
            if (locale.isNotBlank()) {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, locale)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, locale)
            }
        }
        val recognizerKind = if (useOnDeviceRecognizer && Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)) "on-device" else "network"
        emit(EventKind.RESTART, "starting recognizer=$recognizerKind locale='${locale.ifBlank { "(none)" }}' preferOffline=$preferOffline")
        try {
            sr.startListening(intent)
        } catch (e: Exception) {
            Log.w(tag, "startListening failed; will retry", e)
            emit(EventKind.ERROR, "startListening exception: ${e.javaClass.simpleName}: ${e.message}")
            restartShortly()
        }
    }

    private fun restartShortly(delayMs: Long = 100) {
        if (!wantsRestart) return
        try { recognizer?.cancel() } catch (_: Exception) {}
        try { recognizer?.destroy() } catch (_: Exception) {}
        recognizer = null
        handler.postDelayed({ startListening() }, delayMs)
    }

    private fun handleCandidate(text: String?) {
        if (text.isNullOrBlank()) return
        val matched = WakePhraseMatcher.matches(text, phrase)
        emit(if (matched) EventKind.WAKE_MATCH else EventKind.PARTIAL,
             "$text${if (matched) "  <- MATCH for '$phrase'" else ""}")
        if (!matched) return
        Log.i(tag, "Wake phrase matched: '$text' ~= '$phrase'")
        try { recognizer?.cancel() } catch (_: Exception) {}
        wantsRestart = false
        // CRITICAL: startService(), NOT startForegroundService(). See
        // v0.1.2 commit notes for why - mic FGS is anchored upstream by
        // BenForegroundService.
        val voiceIntent = Intent(this, BenVoiceService::class.java)
            .setAction(BenVoiceService.ACTION_START_FROM_WAKE)
        try {
            startService(voiceIntent)
        } catch (e: IllegalStateException) {
            Log.w(tag, "startService(BenVoiceService) blocked", e)
            emit(EventKind.ERROR, "startService(BenVoiceService) blocked: ${e.message}")
        }
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) { emit(EventKind.READY, "") }
        override fun onBeginningOfSpeech() { emit(EventKind.BEGIN, "") }
        override fun onRmsChanged(rmsdB: Float) { /* too noisy to broadcast */ }
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {
            emit(EventKind.RESTART, "endOfSpeech -> restart")
            restartShortly()
        }
        override fun onError(error: Int) {
            val name = errorName(error)
            emit(EventKind.ERROR, "$name (code=$error)")
            consecutiveErrors++

            // ERROR_LANGUAGE_UNAVAILABLE / ERROR_LANGUAGE_NOT_SUPPORTED:
            // walk the candidate locale list AND force the network recognizer.
            // No need to wait two errors - a locale mismatch is permanent
            // until we change the request, so spinning on it is pointless.
            if (error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE ||
                error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED) {
                val wasOnDevice = useOnDeviceRecognizer
                val oldLocale = currentLocale
                if (localeIndex < candidateLocales.lastIndex) {
                    localeIndex++
                    emit(EventKind.RESTART, "locale '${oldLocale.ifBlank { "(none)" }}' unavailable -> retry with '${currentLocale.ifBlank { "(none)" }}'")
                } else if (wasOnDevice) {
                    // Exhausted locales on the on-device recognizer.
                    // Switch to network recognizer and rewind the locale
                    // walk - the network side often supports more locales.
                    useOnDeviceRecognizer = false
                    preferOffline = false
                    localeIndex = 0
                    emit(EventKind.RESTART, "exhausted locales on on-device recognizer -> switching to NETWORK recognizer")
                    Log.w(tag, "Locale list exhausted on on-device recognizer; falling back to network recognizer")
                } else {
                    // Already on network recognizer and still failing.
                    // Drop the locale extra entirely (empty index) and
                    // hope the recognizer defaults to whatever it has.
                    localeIndex = candidateLocales.indexOf("").coerceAtLeast(candidateLocales.lastIndex)
                    emit(EventKind.ERROR, "locale list exhausted on network recognizer; falling back to no-locale request")
                    Log.w(tag, "Locale list exhausted on network recognizer; falling back to no-locale request")
                }
                restartShortly(150)
                return
            }

            // After two consecutive recoverable errors, flip off offline
            // preference so we try the network recognizer once. Most likely
            // cause of repeated ERROR_NO_MATCH / ERROR_SPEECH_TIMEOUT on a
            // fresh device is a missing offline en-US pack.
            if (consecutiveErrors >= 2 && preferOffline &&
                (error == SpeechRecognizer.ERROR_NO_MATCH ||
                 error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT)) {
                preferOffline = false
                useOnDeviceRecognizer = false
                emit(EventKind.RESTART, "two consecutive recoverable errors -> falling back to network recognizer")
                Log.w(tag, "Falling back to network recognizer after $consecutiveErrors consecutive recoverable errors")
            }
            restartShortly(150)
        }
        override fun onResults(results: Bundle?) {
            val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            val text = list?.firstOrNull()
            if (!text.isNullOrBlank()) {
                consecutiveErrors = 0
                emit(EventKind.RESULT, text)
                handleCandidate(text)
            }
            restartShortly()
        }
        override fun onPartialResults(partialResults: Bundle?) {
            val list = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            val text = list?.firstOrNull()
            if (!text.isNullOrBlank()) {
                consecutiveErrors = 0
                handleCandidate(text)
            }
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    /** Append to the ring buffer + LocalBroadcastManager broadcast for MicTestActivity. */
    private fun emit(kind: EventKind, detail: String) {
        val ts = System.currentTimeMillis()
        val ev = Event(ts, kind, detail)
        eventBuffer.offer(ev)
        while (eventBuffer.size > MAX_EVENTS) eventBuffer.poll()
        val intent = Intent(ACTION_EVENT)
            .putExtra(EXTRA_TS, ts)
            .putExtra(EXTRA_KIND, kind.name)
            .putExtra(EXTRA_DETAIL, detail)
        LocalBroadcastManager.getInstance(applicationContext).sendBroadcast(intent)
        Log.d(tag, "[${kind.name}] $detail")
    }

    private fun errorName(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_AUDIO -> "ERROR_AUDIO"
        SpeechRecognizer.ERROR_CLIENT -> "ERROR_CLIENT"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "ERROR_INSUFFICIENT_PERMISSIONS (RECORD_AUDIO not granted!)"
        SpeechRecognizer.ERROR_NETWORK -> "ERROR_NETWORK"
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "ERROR_NETWORK_TIMEOUT"
        SpeechRecognizer.ERROR_NO_MATCH -> "ERROR_NO_MATCH"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "ERROR_RECOGNIZER_BUSY"
        SpeechRecognizer.ERROR_SERVER -> "ERROR_SERVER"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "ERROR_SPEECH_TIMEOUT (no speech detected)"
        SpeechRecognizer.ERROR_TOO_MANY_REQUESTS -> "ERROR_TOO_MANY_REQUESTS"
        SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED -> "ERROR_LANGUAGE_NOT_SUPPORTED (install en-US offline pack)"
        SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE -> "ERROR_LANGUAGE_UNAVAILABLE (install en-US offline pack)"
        SpeechRecognizer.ERROR_SERVER_DISCONNECTED -> "ERROR_SERVER_DISCONNECTED"
        else -> "ERROR_$error"
    }

    enum class EventKind { READY, BEGIN, PARTIAL, RESULT, WAKE_MATCH, ERROR, RESTART }
    data class Event(val ts: Long, val kind: EventKind, val detail: String)

    companion object {
        const val ACTION_PAUSE = "com.ben.wake.PAUSE"
        const val ACTION_RESUME = "com.ben.wake.RESUME"
        const val ACTION_RELOAD_PHRASE = "com.ben.wake.RELOAD_PHRASE"
        const val ACTION_FORCE_RESTART = "com.ben.wake.FORCE_RESTART"

        const val ACTION_EVENT = "com.ben.wake.EVENT"
        const val EXTRA_TS = "ts"
        const val EXTRA_KIND = "kind"
        const val EXTRA_DETAIL = "detail"

        // Set just beyond BenVoiceService's 600s hard cap (v0.1.3) so a
        // healthy long session does not get cut off.
        private const val DEFENSIVE_RESUME_MS: Long = 620_000L
        private const val MAX_EVENTS = 200

        // Shared ring buffer that MicTestActivity reads on launch (so users
        // see history even before subscribing to live events).
        val eventBuffer = ConcurrentLinkedDeque<Event>()

        fun pause(ctx: Context) {
            val intent = Intent(ctx, BenWakewordService::class.java).setAction(ACTION_PAUSE)
            try { ctx.startService(intent) } catch (e: Exception) { Log.w("BenWakewordService", "pause failed", e) }
        }

        fun resume(ctx: Context) {
            val intent = Intent(ctx, BenWakewordService::class.java).setAction(ACTION_RESUME)
            try { ctx.startService(intent) } catch (e: Exception) { Log.w("BenWakewordService", "resume failed", e) }
        }

        fun forceRestart(ctx: Context) {
            val intent = Intent(ctx, BenWakewordService::class.java).setAction(ACTION_FORCE_RESTART)
            try { ctx.startService(intent) } catch (e: Exception) { Log.w("BenWakewordService", "forceRestart failed", e) }
        }
    }
}
