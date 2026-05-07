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
import com.ben.util.BenSecrets
import com.ben.wake.WakePhraseMatcher

/**
 * Always-on wake word listener. Uses Android's on-device SpeechRecognizer in
 * partial-results mode and runs an auto-restart loop because the system
 * recognizer stops on every silence boundary.
 *
 * On a partial transcript that fuzzy-matches the user's configured phrase, we
 * cancel the recognizer and hand off to BenVoiceService (OpenAI Realtime).
 *
 * NO audio leaves the device until that handoff happens.
 *
 * Fallback path (not in v1): if no partials show up within 3s during the first
 * post-onboarding self-test, we surface a notification suggesting the user
 * install the offline language pack OR opt in to the Vosk fallback. Detection
 * lives in BenWakewordSelfTest below; production flow stays simple.
 */
class BenWakewordService : Service() {
    private val tag = "BenWakewordService"
    private var recognizer: SpeechRecognizer? = null
    private val handler = Handler(Looper.getMainLooper())
    private var phrase: String = "Ben"
    private var paused: Boolean = false
    private var wantsRestart: Boolean = true

    // Defensive auto-resume: if BenVoiceService crashes (or its 180s hard
    // cap fails to fire), we'd otherwise stay paused forever and never wake
    // again until process restart. After DEFENSIVE_RESUME_MS we force a
    // resume even if no ACTION_RESUME has arrived. The window is set just
    // beyond the voice service's 180s hard cap so we do not collide with
    // a healthy long-running session.
    private val defensiveResumeRunnable = Runnable {
        Log.w(tag, "Defensive auto-resume: no ACTION_RESUME after pause - re-arming wake listener")
        paused = false
        wantsRestart = true
        restartShortly()
    }

    override fun onCreate() {
        super.onCreate()
        // Intentionally NOT a foreground service: mic access is inherited from
        // BenForegroundService's foregroundServiceType="microphone". Posting
        // our own startForeground notification here would mean the user sees
        // two near-identical "Listening for Ben" entries.
        phrase = BenSecrets.wakePhrase(this)
        startListening()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> {
                paused = true
                try { recognizer?.cancel() } catch (_: Exception) {}
                handler.removeCallbacks(defensiveResumeRunnable)
                handler.postDelayed(defensiveResumeRunnable, DEFENSIVE_RESUME_MS)
            }
            ACTION_RESUME -> {
                handler.removeCallbacks(defensiveResumeRunnable)
                paused = false
                // Critical: handleCandidate() flips wantsRestart=false on a
                // wake match so the recognizer stops chasing more wakes. We
                // MUST reset it here, otherwise startListening()/restartShortly()
                // both short-circuit and the listener never resumes.
                wantsRestart = true
                restartShortly()
            }
            ACTION_RELOAD_PHRASE -> { phrase = BenSecrets.wakePhrase(this) }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?) = null

    override fun onDestroy() {
        super.onDestroy()
        wantsRestart = false
        try { recognizer?.destroy() } catch (_: Exception) {}
        handler.removeCallbacksAndMessages(null)
    }

    private fun startListening() {
        if (paused || !wantsRestart) return
        val ctx = applicationContext
        val sr = if (Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(ctx)) {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(ctx)
        } else {
            SpeechRecognizer.createSpeechRecognizer(ctx)
        }
        sr.setRecognitionListener(listener)
        recognizer = sr
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1500L)
            // Pin to English regardless of the system locale. The wake phrase
            // is the English string "Ben"; on a hi-IN device the default
            // recognizer was transcribing Hindi which both missed real wakes
            // and produced Latin fragments that fuzzy-matched random noise.
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-US")
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "en-US")
            putExtra(RecognizerIntent.EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE, true)
        }
        try {
            sr.startListening(intent)
        } catch (e: Exception) {
            Log.w(tag, "startListening failed; will retry", e)
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
        if (!WakePhraseMatcher.matches(text, phrase)) return
        Log.i(tag, "Wake phrase matched: '$text' ~= '$phrase'")
        try { recognizer?.cancel() } catch (_: Exception) {}
        wantsRestart = false
        // Handoff: BenVoiceService will run for the duration of the session,
        // then signal us via ACTION_RESUME when it ends so we re-arm.
        //
        // CRITICAL: this MUST stay startService(), NOT startForegroundService().
        // BenVoiceService no longer calls startForeground() in onCreate (the
        // duplicate "Conversation in progress" notification was removed in
        // 0.1.1) and its foregroundServiceType="microphone" declaration was
        // dropped from the manifest. If we call startForegroundService here
        // we hand the system a contract the service cannot fulfil, and
        // Android raises RemoteServiceException ~5s later, killing the
        // session mid-conversation. Mic FGS is anchored by BenForegroundService
        // which lives in the same process, so a regular service inherits
        // mic access without itself being a FGS.
        val voiceIntent = Intent(this, BenVoiceService::class.java)
            .setAction(BenVoiceService.ACTION_START_FROM_WAKE)
        try {
            startService(voiceIntent)
        } catch (e: IllegalStateException) {
            // Background-start restriction: shouldn't happen because we're
            // running inside BenForegroundService's process, but log
            // defensively so the next attempt has telemetry.
            Log.w(tag, "startService(BenVoiceService) blocked", e)
        }
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() { restartShortly() }
        override fun onError(error: Int) { restartShortly(150) }
        override fun onResults(results: Bundle?) {
            val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            handleCandidate(list?.firstOrNull())
            restartShortly()
        }
        override fun onPartialResults(partialResults: Bundle?) {
            val list = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            handleCandidate(list?.firstOrNull())
        }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    companion object {
        const val ACTION_PAUSE = "com.ben.wake.PAUSE"
        const val ACTION_RESUME = "com.ben.wake.RESUME"
        const val ACTION_RELOAD_PHRASE = "com.ben.wake.RELOAD_PHRASE"

        // Set just beyond BenVoiceService's 180s hard cap so a healthy long
        // session does not get cut off, but a crashed session still recovers
        // within ~20s of when it should have ended.
        private const val DEFENSIVE_RESUME_MS: Long = 200_000L

        /** Pause the wake recognizer. Used by BenVoiceService while a session
         * is live so that speaker output (TTS playback) cannot self-trigger
         * the wake word and start a feedback loop. */
        fun pause(ctx: Context) {
            val intent = Intent(ctx, BenWakewordService::class.java).setAction(ACTION_PAUSE)
            try {
                ctx.startService(intent)
            } catch (e: Exception) {
                Log.w("BenWakewordService", "pause failed", e)
            }
        }

        fun resume(ctx: Context) {
            val intent = Intent(ctx, BenWakewordService::class.java).setAction(ACTION_RESUME)
            try {
                ctx.startService(intent)
            } catch (e: Exception) {
                Log.w("BenWakewordService", "resume failed", e)
            }
        }
    }
}
