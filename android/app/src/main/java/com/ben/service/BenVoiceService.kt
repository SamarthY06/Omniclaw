package com.ben.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Base64
import android.util.Log
import com.ben.util.BenSecrets
import com.ben.util.CallKind
import com.ben.util.CostLedger
import com.ben.util.OpenAiConfig
import com.ben.wake.WakePhraseMatcher
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.Socket
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.ExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * Realtime voice loop. After the wake word fires, this service:
 *
 *   1. Connects to OpenAI's Realtime WSS endpoint with the user's API key.
 *   2. Streams 24 kHz PCM16 mic audio with `input_audio_buffer.append`.
 *   3. Plays back audio.delta events as PCM16 to AudioTrack.
 *   4. Forwards model tool-calls into the embedded Node bridge so they hit
 *      OpenClaw and ultimately Android AccessibilityService / vision tools.
 *
 * Session-end conditions (any one of these triggers stopAndRearm()):
 *   a) 3 min silence after a model response.done with no new speech_started.
 *      Per user spec: after Ben replies, you have 3 min to keep talking.
 *   b) Explicit stop intent ("stop", "shut up", "i'm not talking to you").
 *   c) Hard 600 s (10 min) ceiling regardless of activity.
 *   d) WebSocket failure / closure with reconnect-with-backoff exhausted.
 *   e) CostLedger refusal (daily / monthly cap exceeded - user is told
 *      via a one-shot system message and the session ends gracefully).
 *
 * Audio capture pipeline:
 *   * AudioSource.VOICE_RECOGNITION (tuned for distant speech, mild AGC).
 *   * If AcousticEchoCanceler is available on the device, we attach it to
 *     the AudioRecord session - this is hardware AEC, much better than
 *     our isAssistantSpeaking gate at suppressing the speaker -> mic
 *     bounce. The is-speaking gate stays in place as a backstop for
 *     devices without hardware AEC.
 *   * NoiseSuppressor + AutomaticGainControl attached when available; both
 *     are silently no-op'd on devices that lack them.
 *
 * Reconnect-with-backoff:
 *   onFailure / onClosed used to call stopAndRearm() unconditionally,
 *   which made one transient WSS hiccup tear down the entire session and
 *   forced the user to re-wake. We now retry up to RECONNECT_MAX_ATTEMPTS
 *   times with exponential backoff (500/1000/2000 ms) before giving up.
 *
 * Notification ownership: this service is intentionally NOT a foreground
 * service. The microphone foreground anchor is BenForegroundService.
 */
class BenVoiceService : Service() {
    private val tag = "BenVoiceService"
    private val httpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private var websocket: WebSocket? = null
    private val running = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioTrack: AudioTrack? = null
    private var sessionId: String = ""

    @Volatile private var isAssistantSpeaking: Boolean = false

    /**
     * v0.1.9: total PCM bytes written to AudioTrack so far in this session.
     * Used by `unmuteWhenAudioDrains()` to compare against
     * `AudioTrack.getPlaybackHeadPosition()` (in frames) - we only flip the
     * mic mute off when the playback head has reached the write head, i.e.
     * the model's tail audio is no longer in the speaker. This was the
     * silent root cause of the 0.1.5-0.1.8 echo loop (mic gate opened on
     * response.done while AudioTrack was still flushing the last 200-800ms
     * of audio out the speaker).
     *
     * 16-bit PCM mono, so 2 bytes per frame. Reset to 0 at every
     * stopAndRearm() (new session = fresh AudioTrack, fresh write head).
     */
    @Volatile private var bytesWrittenToAudioTrack: Long = 0L

    /**
     * v0.1.9: the last completed assistant audio transcript (from
     * `response.audio_transcript.done`). Used by `shouldRespondTo()` to
     * reject incoming whisper transcripts that are too similar to what
     * the model JUST said - those are almost always speaker bleed
     * mis-identified as a new user turn (the "Opening WhatsApp" ->
     * "Binning WhatsApp" echo loop). Reset at stopAndRearm().
     */
    @Volatile private var lastAssistantTranscript: String = ""
    private val mainHandler = Handler(Looper.getMainLooper())
    private val silenceEndRunnable = Runnable {
        Log.i(tag, "post-response silence reached - ending session")
        stopAndRearm()
    }

    /**
     * v0.1.9: drain-poll Runnable for the mic-unmute-after-tail logic.
     * Re-posts itself every 50 ms until either AudioTrack.playbackHeadPosition
     * has caught up to bytesWrittenToAudioTrack (i.e. the model's tail audio
     * is fully out of the speaker) OR the absolute fallback timeout fires.
     * See OpenAiConfig.MIC_MUTE_TAIL_MS for the fallback ceiling.
     */
    @Volatile private var drainPollDeadlineMs: Long = 0L
    private val drainPollRunnable: Runnable = object : Runnable {
        override fun run() {
            val track = audioTrack
            val writeFrames = bytesWrittenToAudioTrack / 2  // 16-bit PCM mono
            val playFrames = try { track?.playbackHeadPosition?.toLong() ?: -1L } catch (_: Exception) { -1L }
            val now = System.currentTimeMillis()
            val drained = (playFrames >= 0 && playFrames >= writeFrames)
            val timedOut = now >= drainPollDeadlineMs
            if (drained || timedOut || !running.get()) {
                isAssistantSpeaking = false
                emitVoiceEvent(
                    this@BenVoiceService,
                    "MIC_UNMUTE",
                    if (drained) "drained head=$playFrames/$writeFrames"
                    else if (timedOut) "fallback_timeout head=$playFrames/$writeFrames"
                    else "session_ended",
                )
            } else {
                mainHandler.postDelayed(this, 50)
            }
        }
    }
    private val hardCapRunnable = Runnable {
        Log.w(tag, "hard session cap reached - ending session")
        stopAndRearm()
    }

    private val reconnectAttempts = AtomicInteger(0)

    /**
     * v0.1.7: Counts how many user transcripts we've actually decided to
     * respond to. Used purely for logging / VoiceNodeBridgeNotifier and to
     * decide whether to short-circuit the silence timer reset on
     * `response.created` events. The auto-greet bug is no longer prevented
     * by this flag (that's done server-side via create_response=false);
     * this is just bookkeeping for the in-app diagnostic UI.
     */
    private val userTurnsAnswered = AtomicInteger(0)

    /** Hardware audio-effect handles, kept so we can release them on stop. */
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var agc: AutomaticGainControl? = null

    private var sessionTools: JSONArray = JSONArray()
    private var userFactsText: String = ""
    private var recentMemoriesText: String = ""

    private data class PendingToolCall(val name: String, val args: StringBuilder)
    private val pendingToolCalls = ConcurrentHashMap<String, PendingToolCall>()

    private val toolExecutor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "ben-tool-dispatch").apply { isDaemon = true }
    }

    /**
     * v0.1.9: bootstrap executor for the IO-heavy session-start path.
     * `onStartCommand` runs on the main thread; calling `connect()`
     * directly from there means `fetchToolsFromBridge()` opens a Socket
     * on the main thread and Android throws `NetworkOnMainThreadException`
     * before tools can be loaded. This was the silent root cause of
     * every "Ben just chats and never opens WhatsApp" report from
     * 0.1.5 - 0.1.8 - sessionTools always ended up empty. We dispatch
     * the entire `connect()` to this single-thread executor so the IO
     * is legal, then the post-bootstrap work (mic loop / audio playback)
     * starts its own threads.
     */
    private val bootstrapExecutor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "ben-voice-bootstrap").apply { isDaemon = true }
    }

    override fun onCreate() {
        super.onCreate()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START_FROM_WAKE, ACTION_START_FROM_USER -> {
                if (!running.compareAndSet(false, true)) return START_NOT_STICKY
                sessionId = "sess_${UUID.randomUUID().toString().replace("-", "").take(16)}"
                BenWakewordService.pause(this)
                BenForegroundService.setActive(this, true)
                mainHandler.postDelayed(hardCapRunnable, OpenAiConfig.HARD_SESSION_CAP_MS)
                // v0.1.9: connect() must run off-main because it opens
                // sockets to the embedded Node bridge for tools.list and
                // session.context. See bootstrapExecutor doc above.
                bootstrapExecutor.execute { connect() }
            }
            ACTION_STOP -> stopAndRearm()
        }
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        try { BenWakewordService.resume(this) } catch (_: Exception) {}
        stopAndRearm()
    }

    private fun stopAndRearm() {
        if (!running.compareAndSet(true, false)) return
        emitVoiceEvent(this, "SESSION_END", "turns=${userTurnsAnswered.get()}")
        mainHandler.removeCallbacks(silenceEndRunnable)
        mainHandler.removeCallbacks(hardCapRunnable)
        mainHandler.removeCallbacks(drainPollRunnable)
        isAssistantSpeaking = false
        userTurnsAnswered.set(0)
        bytesWrittenToAudioTrack = 0L
        lastAssistantTranscript = ""
        pendingToolCalls.clear()
        try { websocket?.close(1000, "session_ended") } catch (_: Exception) {}
        websocket = null
        try { recordThread?.interrupt() } catch (_: Exception) {}
        recordThread = null
        try { audioTrack?.stop(); audioTrack?.release() } catch (_: Exception) {}
        audioTrack = null
        // Release hardware audio effects. Skipping this leaks the kernel
        // session id and eventually we run out of effect slots.
        try { aec?.release() } catch (_: Exception) {}
        try { ns?.release() } catch (_: Exception) {}
        try { agc?.release() } catch (_: Exception) {}
        aec = null; ns = null; agc = null
        BenForegroundService.setActive(this, false)
        BenWakewordService.resume(this)
        VoiceNodeBridgeNotifier.notifySessionEnded(this, sessionId)
        try { toolExecutor.shutdownNow() } catch (_: Exception) {}
        try { bootstrapExecutor.shutdownNow() } catch (_: Exception) {}
        stopSelf()
    }

    private fun connect() {
        // v0.1.9 diagnostic: prove which thread we're on. Should be
        // "ben-voice-bootstrap" - if it's "main" then the dispatch from
        // onStartCommand regressed and Socket calls below will throw
        // NetworkOnMainThreadException, sessionTools will end up empty,
        // and the model will be unable to call any tool.
        emitVoiceEvent(this, "BOOTSTRAP_THREAD", Thread.currentThread().name)
        val apiKey = BenSecrets.openaiKey(this)
        if (apiKey.isNullOrBlank()) {
            Log.w(tag, "No OpenAI key - aborting session")
            stopAndRearm()
            return
        }
        // Cost-cap pre-check: if the user has already exceeded their daily /
        // monthly cap, refuse to even open the WSS. The model never gets
        // a chance to start a turn.
        val refusal = CostLedger.checkRefusal(this)
        if (refusal != null) {
            Log.w(tag, "session refused by cost ledger: $refusal")
            // Best-effort: announce the refusal via the audio track so the
            // user gets some feedback (otherwise the wake fires and nothing
            // visible happens). On Realtime we'd normally TTS this; without
            // a session we just log it. The Mic Test screen surfaces the
            // ledger state for diagnosis.
            stopAndRearm()
            return
        }
        VoiceNodeBridgeNotifier.notifySessionStarted(this, sessionId)
        // v0.1.8: retry tools.list up to 3 times with 400 ms between attempts.
        // Right after wake, the Node bridge may still be booting (especially
        // on a cold app launch) and the first fetch returns empty. Empirically
        // this is the leading cause of "model just chats and never calls
        // tools" - sessionTools=[] gets shipped to OpenAI in session.update
        // and the model literally has no function-calling vocabulary.
        var tools = JSONArray()
        var lastErr: String? = null
        for (attempt in 1..3) {
            tools = try {
                fetchToolsFromBridge()
            } catch (e: Exception) {
                lastErr = "${e.javaClass.simpleName}: ${e.message ?: ""}"
                JSONArray()
            }
            if (tools.length() > 0) break
            try { Thread.sleep(400) } catch (_: InterruptedException) {}
        }
        sessionTools = tools
        if (sessionTools.length() == 0) {
            emitVoiceEvent(this, "TOOLS_FETCH", "FAILED after 3 attempts: ${lastErr ?: "empty array returned"}")
        } else {
            emitVoiceEvent(this, "TOOLS_FETCH", "ok n=${sessionTools.length()}")
        }
        Log.i(tag, "session tools count=${sessionTools.length()}")
        if (sessionTools.length() == 0) {
            // v0.1.9: hard-refuse the session. Without tools the model can
            // only chat - which leads to the "Sure, I'll open WhatsApp"
            // verbal lies the user hated. Better to TTS a single honest
            // sentence and end the session cleanly so the user knows to
            // try again rather than monologue for 5 minutes pretending
            // everything is fine.
            emitVoiceEvent(this, "TOOLS_EMPTY", "Node bridge returned no tools - refusing session")
            emitVoiceEvent(this, "SESSION_REFUSED", "no_tools")
            speakRefusalAndEnd(apiKey, "My device tools didn't load this time. Please try again in a moment.")
            return
        } else {
            val names = StringBuilder()
            for (i in 0 until minOf(5, sessionTools.length())) {
                val t = sessionTools.optJSONObject(i) ?: continue
                if (names.isNotEmpty()) names.append(",")
                names.append(t.optString("name", "?"))
            }
            emitVoiceEvent(this, "TOOLS_READY", "n=${sessionTools.length()} first=[$names]")
        }
        try {
            val ctx = fetchSessionContextFromBridge()
            userFactsText = ctx.optString("user_facts", "").trim()
            val mem = ctx.optJSONObject("memory")
            recentMemoriesText = if (mem != null) formatMemoryMatches(mem) else ""
            Log.i(
                tag,
                "session.context user_facts_present=${ctx.optBoolean("user_facts_present", false)} " +
                    "memory_total=${mem?.optInt("total", 0) ?: 0}",
            )
        } catch (e: Exception) {
            Log.w(tag, "session.context failed (${e.message}); proceeding without user facts")
            userFactsText = ""
            recentMemoriesText = ""
        }
        reconnectAttempts.set(0)
        emitVoiceEvent(this, "SESSION_START", "tools=${sessionTools.length()} facts=${userFactsText.length>0}")
        openWss(apiKey)
        startMicLoop()
        startAudioPlayback()
    }

    /**
     * Open the Realtime WSS with the configured model + auth headers. Used
     * both for the initial connect() and for reconnect-with-backoff.
     */
    private fun openWss(apiKey: String) {
        val req = Request.Builder()
            .url("${OpenAiConfig.REALTIME_WSS_HOST}/v1/realtime?model=${OpenAiConfig.REALTIME_MODEL}")
            .header("Authorization", "Bearer $apiKey")
            .header("OpenAI-Beta", OpenAiConfig.REALTIME_BETA_HEADER)
            .build()
        websocket = httpClient.newWebSocket(req, listener)
    }

    /**
     * Schedule a reconnect attempt after the configured backoff. Returns
     * false if we've exhausted RECONNECT_MAX_ATTEMPTS (caller should
     * stopAndRearm).
     */
    private fun scheduleReconnect(): Boolean {
        if (!running.get()) return false
        val attempt = reconnectAttempts.getAndIncrement()
        if (attempt >= OpenAiConfig.RECONNECT_MAX_ATTEMPTS) {
            Log.w(tag, "reconnect: exhausted ${OpenAiConfig.RECONNECT_MAX_ATTEMPTS} attempts; ending session")
            return false
        }
        val backoff = OpenAiConfig.RECONNECT_BACKOFFS_MS.getOrElse(attempt) {
            OpenAiConfig.RECONNECT_BACKOFFS_MS.last()
        }
        Log.i(tag, "reconnect: scheduling attempt ${attempt + 1} in ${backoff} ms")
        val apiKey = BenSecrets.openaiKey(this) ?: return false
        mainHandler.postDelayed({
            if (!running.get()) return@postDelayed
            try { websocket?.close(1011, "reconnecting") } catch (_: Exception) {}
            websocket = null
            openWss(apiKey)
        }, backoff)
        return true
    }

    private fun fetchToolsFromBridge(): JSONArray {
        val req = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("method", "tools.list")
            .put("params", JSONObject())
        Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress(InetAddress.getLoopbackAddress(), OpenAiConfig.NODE_BRIDGE_PORT), 1500)
            sock.soTimeout = 2500
            val out = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
            out.write(req.toString())
            out.write("\n")
            out.flush()
            val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            val line = reader.readLine() ?: return JSONArray()
            val resp = JSONObject(line)
            if (resp.has("error")) {
                Log.w(tag, "tools.list bridge error: ${resp.optString("error")}")
                return JSONArray()
            }
            return resp.optJSONObject("result")?.optJSONArray("tools") ?: JSONArray()
        }
    }

    private fun fetchSessionContextFromBridge(): JSONObject {
        val req = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("method", "session.context")
            .put("params", JSONObject().put("memory_limit", 8))
        Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress(InetAddress.getLoopbackAddress(), OpenAiConfig.NODE_BRIDGE_PORT), 1500)
            sock.soTimeout = 2500
            val out = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
            out.write(req.toString())
            out.write("\n")
            out.flush()
            val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            val line = reader.readLine() ?: return JSONObject()
            val resp = JSONObject(line)
            if (resp.has("error")) {
                Log.w(tag, "session.context bridge error: ${resp.optString("error")}")
                return JSONObject()
            }
            return resp.optJSONObject("result") ?: JSONObject()
        }
    }

    private fun formatMemoryMatches(mem: JSONObject): String {
        val matches = mem.optJSONArray("matches") ?: return ""
        if (matches.length() == 0) return ""
        val sb = StringBuilder()
        for (i in 0 until matches.length()) {
            val m = matches.optJSONObject(i) ?: continue
            val key = m.optString("key", "")
            val rawValue = m.opt("value")
            val valueStr = when (rawValue) {
                null, JSONObject.NULL -> ""
                is String -> rawValue
                else -> rawValue.toString()
            }
            val trimmed = if (valueStr.length > 120) valueStr.substring(0, 117) + "..." else valueStr
            sb.append("- ").append(key).append(": ").append(trimmed).append('\n')
        }
        return sb.toString().trimEnd()
    }

    private fun invokeToolViaBridge(name: String, argsJson: String): JSONObject {
        val params = JSONObject()
            .put("name", name)
            .put("args", if (argsJson.isBlank()) JSONObject() else JSONObject(argsJson))
        val req = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("method", "tools.invoke")
            .put("params", params)
        Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress(InetAddress.getLoopbackAddress(), OpenAiConfig.NODE_BRIDGE_PORT), 2_000)
            sock.soTimeout = OpenAiConfig.TOOL_INVOKE_TIMEOUT_MS
            val out = OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
            out.write(req.toString())
            out.write("\n")
            out.flush()
            val reader = BufferedReader(InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            val line = reader.readLine() ?: return JSONObject().put("ok", false).put("error", "bridge_no_response")
            val resp = JSONObject(line)
            if (resp.has("error")) {
                return JSONObject().put("ok", false).put("error", resp.optString("error"))
            }
            return resp.optJSONObject("result") ?: JSONObject().put("ok", false).put("error", "bridge_empty_result")
        }
    }

    private fun startMicLoop() {
        val sampleRate = OpenAiConfig.REALTIME_SAMPLE_RATE_HZ
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val encoding = AudioFormat.ENCODING_PCM_16BIT
        val minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, encoding)
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate, channelConfig, encoding, minBuffer * 4)
        } catch (e: SecurityException) {
            Log.e(tag, "RECORD_AUDIO denied", e); stopAndRearm(); return
        }
        // Attach hardware audio-effects when the device supports them.
        // Each is wrapped in its own try/catch because individual chips
        // sometimes claim isAvailable()=true and then NPE on .create.
        attachAudioEffects(rec.audioSessionId)
        rec.startRecording()
        val buf = ByteArray(minBuffer.coerceAtLeast(960 * 2))
        recordThread = Thread({
            try {
                while (running.get() && !Thread.currentThread().isInterrupted) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    // Backstop for devices without hardware AEC: drop
                    // frames captured while the model is speaking. We
                    // still drain AudioRecord so the kernel buffer
                    // doesn't back up.
                    if (isAssistantSpeaking) continue
                    val b64 = Base64.encodeToString(buf, 0, n, Base64.NO_WRAP)
                    val ev = JSONObject().put("type", "input_audio_buffer.append").put("audio", b64)
                    websocket?.send(ev.toString())
                }
            } finally {
                try { rec.stop(); rec.release() } catch (_: Exception) {}
            }
        }, "ben-mic").also { it.start() }
    }

    private fun attachAudioEffects(sessionId: Int) {
        try {
            if (AcousticEchoCanceler.isAvailable()) {
                aec = AcousticEchoCanceler.create(sessionId)?.apply { enabled = true }
                Log.i(tag, "AEC ${if (aec?.enabled == true) "ENABLED" else "unavailable"}")
            } else {
                Log.i(tag, "AEC not available on this device - falling back to is-speaking gate")
            }
        } catch (e: Exception) { Log.w(tag, "AEC attach failed", e) }
        try {
            if (NoiseSuppressor.isAvailable()) {
                ns = NoiseSuppressor.create(sessionId)?.apply { enabled = true }
                Log.i(tag, "NS ${if (ns?.enabled == true) "ENABLED" else "unavailable"}")
            }
        } catch (e: Exception) { Log.w(tag, "NS attach failed", e) }
        try {
            if (AutomaticGainControl.isAvailable()) {
                agc = AutomaticGainControl.create(sessionId)?.apply { enabled = true }
                Log.i(tag, "AGC ${if (agc?.enabled == true) "ENABLED" else "unavailable"}")
            }
        } catch (e: Exception) { Log.w(tag, "AGC attach failed", e) }
    }

    private fun startAudioPlayback() {
        val sampleRate = OpenAiConfig.REALTIME_SAMPLE_RATE_HZ
        val attrs = AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(sampleRate)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .build()
        val bufferSize = AudioTrack.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(attrs)
            .setAudioFormat(format)
            .setBufferSizeInBytes(bufferSize.coerceAtLeast(48_000))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
            .also { it.play() }
    }

    private val listener = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(tag, "Realtime WSS open")
            emitVoiceEvent(this@BenVoiceService, "WSS_OPEN", "code=${response.code}")
            // We made it through the WSS handshake, so reset the reconnect
            // counter for the next failure window.
            reconnectAttempts.set(0)
            val sysPrompt = buildSysPrompt()
            val turnDetection = JSONObject()
                .put("type", "server_vad")
                .put("threshold", OpenAiConfig.VAD_THRESHOLD)
                .put("prefix_padding_ms", OpenAiConfig.VAD_PREFIX_PADDING_MS)
                .put("silence_duration_ms", OpenAiConfig.VAD_SILENCE_DURATION_MS)
                // v0.1.7: create_response is FALSE. The server VAD still
                // commits audio buffers on silence and emits transcription
                // events, but we manually call response.create only when
                // we've decided the transcript is worth responding to (not
                // just the wake word, not just ambient noise). This is the
                // bulletproof fix for the "model auto-greets when user has
                // not asked anything" bug - the previous v0.1.6 cancel-
                // after-the-fact approach didn't work because the wake
                // phrase audio itself ("hey ben") got transcribed and our
                // userHasSpokenThisSession flag flipped before we could
                // cancel the auto-response.
                .put("create_response", false)
                .put("interrupt_response", true)
            val transcription = JSONObject()
                .put("model", OpenAiConfig.REALTIME_TRANSCRIPTION_MODEL)
                .put("language", OpenAiConfig.REALTIME_LANGUAGE)
            val update = JSONObject()
                .put("type", "session.update")
                .put("session", JSONObject()
                    .put("modalities", JSONArray().put("audio").put("text"))
                    .put("instructions", sysPrompt)
                    .put("voice", OpenAiConfig.REALTIME_VOICE)
                    .put("input_audio_format", OpenAiConfig.REALTIME_AUDIO_FORMAT)
                    .put("output_audio_format", OpenAiConfig.REALTIME_AUDIO_FORMAT)
                    .put("input_audio_transcription", transcription)
                    .put("turn_detection", turnDetection)
                    .put("tools", sessionTools)
                    .put("tool_choice", "auto")
                    .put("max_response_output_tokens", OpenAiConfig.REALTIME_MAX_RESPONSE_OUTPUT_TOKENS))
            webSocket.send(update.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val ev = JSONObject(text)
                when (val type = ev.optString("type")) {
                    "response.created" -> {
                        // With create_response=false in session.update, a
                        // response.created event only fires when WE called
                        // response.create from
                        // conversation.item.input_audio_transcription.completed
                        // below. So if we're here, the user has been
                        // explicitly heard.
                        isAssistantSpeaking = true
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.audio.delta" -> {
                        val b64 = ev.optString("delta")
                        if (b64.isNotBlank()) {
                            isAssistantSpeaking = true
                            mainHandler.removeCallbacks(silenceEndRunnable)
                            // v0.1.9: cancel any pending drain-poll Runnable
                            // because more audio just arrived - the drain
                            // window restarts after the LAST delta.
                            mainHandler.removeCallbacks(drainPollRunnable)
                            val pcm = Base64.decode(b64, Base64.DEFAULT)
                            val written = try {
                                audioTrack?.write(pcm, 0, pcm.size) ?: 0
                            } catch (_: Exception) { 0 }
                            if (written > 0) bytesWrittenToAudioTrack += written.toLong()
                            VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "audio.delta")
                        }
                    }
                    "response.audio.done" -> {
                        VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "audio.done")
                    }
                    "response.done" -> {
                        // v0.1.9: do NOT flip isAssistantSpeaking=false here.
                        // The model marks the response complete server-side
                        // but the AudioTrack still has buffered tail audio
                        // playing through the speaker. We start a drain-aware
                        // unmute poll that flips the flag only when the
                        // playback head catches the write head (or after
                        // MIC_MUTE_TAIL_MS as fallback). This is the
                        // bulletproof fix for the echo loop.
                        recordCostFromResponseDone(ev)
                        val responseObj = ev.optJSONObject("response")
                        val status = responseObj?.optString("status", "") ?: ""
                        val outputArr = responseObj?.optJSONArray("output")
                        val outputCount = outputArr?.length() ?: 0
                        val outputKinds = StringBuilder()
                        if (outputArr != null) {
                            for (i in 0 until outputArr.length()) {
                                val item = outputArr.optJSONObject(i) ?: continue
                                if (outputKinds.isNotEmpty()) outputKinds.append(",")
                                outputKinds.append(item.optString("type", "?"))
                            }
                        }
                        // v0.1.9: distinguish cancelled responses (caused by
                        // our own response.cancel from stop-intent) from real
                        // completions to keep MicTest log readable.
                        val eventName = if (status == "cancelled") "RESPONSE_CANCELLED" else "RESPONSE_DONE"
                        emitVoiceEvent(this@BenVoiceService, eventName, "status=$status output=${outputCount}[${outputKinds}]")

                        // Schedule the drain-aware unmute. Cancel any
                        // pending poll first; restart with the configured
                        // fallback deadline.
                        mainHandler.removeCallbacks(drainPollRunnable)
                        drainPollDeadlineMs = System.currentTimeMillis() + OpenAiConfig.MIC_MUTE_TAIL_MS
                        mainHandler.post(drainPollRunnable)

                        if (pendingToolCalls.isEmpty()) {
                            mainHandler.removeCallbacks(silenceEndRunnable)
                            mainHandler.postDelayed(silenceEndRunnable, OpenAiConfig.POST_RESPONSE_SILENCE_MS)
                        } else {
                            Log.d(tag, "response.done with ${pendingToolCalls.size} pending tool(s); deferring silence timer")
                        }
                    }
                    "input_audio_buffer.speech_started" -> {
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.audio_transcript.done" -> {
                        val t = ev.optString("transcript", "")
                        VoiceNodeBridgeNotifier.recordAssistantText(this@BenVoiceService, sessionId, t)
                        emitVoiceEvent(this@BenVoiceService, "ASSISTANT_TEXT", "'${t.take(120)}'")
                        // v0.1.9: stash for the echo-similarity guard in
                        // shouldRespondTo(). Truncated to 200 chars - we
                        // only need a fingerprint, not the full text.
                        lastAssistantTranscript = t.take(200)
                    }
                    "conversation.item.input_audio_transcription.completed" -> {
                        val transcript = ev.optString("transcript", "").trim()
                        VoiceNodeBridgeNotifier.recordUserText(this@BenVoiceService, sessionId, transcript)
                        Log.i(tag, "transcript: '$transcript'")

                        emitVoiceEvent(this@BenVoiceService, "TRANSCRIPT", "'${transcript.take(60)}'")

                        // 1) Stop intents - always honour, end the session.
                        if (isStopIntent(transcript)) {
                            Log.i(tag, "stop intent detected - ending session")
                            emitVoiceEvent(this@BenVoiceService, "STOP_INTENT", "'$transcript'")
                            try { webSocket.send(JSONObject().put("type", "response.cancel").toString()) } catch (_: Exception) {}
                            stopAndRearm()
                            return
                        }

                        // 2) Should we respond? Reject:
                        //    * empty / whitespace transcripts
                        //    * transcripts that are JUST the wake phrase
                        //      (or a fuzzy variant: "ben", "hey ben",
                        //      "okay ben", "ban", "bend", etc.)
                        //    * transcripts shorter than 4 chars total
                        //      (almost always whisper noise)
                        //    * pure-ascii-junk transcripts like "..." or
                        //      "you" or "uh" that whisper emits when it
                        //      hears nothing meaningful.
                        if (!shouldRespondTo(transcript)) {
                            Log.i(tag, "transcript not actionable, skipping response.create: '$transcript'")
                            VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "transcript_skipped:'${transcript.take(40)}'")
                            emitVoiceEvent(this@BenVoiceService, "TRANSCRIPT_SKIP", "no actionable content: '${transcript.take(60)}'")
                            return
                        }

                        // 3) Real user input. Drive a response.
                        userTurnsAnswered.incrementAndGet()
                        Log.i(tag, "transcript actionable - requesting response.create (turn=${userTurnsAnswered.get()})")
                        emitVoiceEvent(this@BenVoiceService, "RESPONSE_CREATE", "turn=${userTurnsAnswered.get()}")
                        try {
                            webSocket.send(JSONObject().put("type", "response.create").toString())
                        } catch (e: Exception) {
                            Log.w(tag, "response.create send failed", e)
                        }
                    }
                    "response.output_item.added" -> {
                        val item = ev.optJSONObject("item") ?: return
                        if (item.optString("type") == "function_call") {
                            val callId = item.optString("call_id").ifBlank { return }
                            val name = item.optString("name", "")
                            pendingToolCalls[callId] = PendingToolCall(name, StringBuilder())
                            Log.i(tag, "tool call started: $name (call_id=$callId)")
                            mainHandler.removeCallbacks(silenceEndRunnable)
                        }
                    }
                    "response.function_call_arguments.delta" -> {
                        val callId = ev.optString("call_id").ifBlank { return }
                        val delta = ev.optString("delta", "")
                        pendingToolCalls[callId]?.args?.append(delta)
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.function_call_arguments.done" -> {
                        val callId = ev.optString("call_id").ifBlank { return }
                        val name = ev.optString("name").ifBlank { pendingToolCalls[callId]?.name ?: "" }
                        val argsStr = ev.optString("arguments")
                            .ifBlank { pendingToolCalls[callId]?.args?.toString() ?: "" }
                        Log.i(tag, "tool call args done: $name (call_id=$callId) args.len=${argsStr.length}")
                        VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "tool.call:$name")
                        emitVoiceEvent(this@BenVoiceService, "TOOL_CALL", "$name args=${argsStr.take(80)}")
                        toolExecutor.execute {
                            dispatchToolCall(webSocket, callId, name, argsStr)
                        }
                    }
                    "session.ended" -> stopAndRearm()
                    "error" -> {
                        Log.w(tag, "Realtime error event: ${ev.optJSONObject("error")?.toString() ?: type}")
                    }
                }
            } catch (e: Exception) {
                Log.w(tag, "onMessage parse: ${e.message}")
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {}
        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.w(tag, "Realtime WSS failed (will retry: ${reconnectAttempts.get() < OpenAiConfig.RECONNECT_MAX_ATTEMPTS})", t)
            emitVoiceEvent(this@BenVoiceService, "WSS_FAILURE", "${t.javaClass.simpleName}:${t.message ?: ""} httpCode=${response?.code ?: -1}")
            if (!scheduleReconnect()) {
                stopAndRearm()
            }
        }
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }
        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            emitVoiceEvent(this@BenVoiceService, "WSS_CLOSED", "code=$code reason=$reason")
            // 1000 = normal closure (we initiated it). 1001 = going away.
            // Anything else is unexpected; try to reconnect.
            val unexpected = (code != 1000 && code != 1001)
            if (unexpected && scheduleReconnect()) return
            stopAndRearm()
        }
    }

    /**
     * Try to extract usage tokens from the Realtime response.done envelope
     * (shape: { response: { usage: { input_tokens, output_tokens, ... } } })
     * and record them against the ledger. Best-effort: a missing usage
     * sub-object is logged at debug level rather than warning, since the
     * field is documented as optional.
     */
    private fun recordCostFromResponseDone(ev: JSONObject) {
        try {
            val usage = ev.optJSONObject("response")?.optJSONObject("usage") ?: return
            val inTokens = usage.optLong("input_tokens", 0L)
            val outTokens = usage.optLong("output_tokens", 0L)
            // Realtime usage covers both audio AND text tokens in one
            // envelope. Without the per-modality breakdown we attribute
            // the whole thing to REALTIME_AUDIO (the dominant cost).
            CostLedger.record(this@BenVoiceService, CallKind.REALTIME_AUDIO, inTokens, outTokens)
        } catch (e: Exception) {
            Log.d(tag, "recordCostFromResponseDone: ${e.message}")
        }
    }

    private fun dispatchToolCall(webSocket: WebSocket, callId: String, name: String, argsJson: String) {
        val output = try {
            val resultEnv = invokeToolViaBridge(name, argsJson)
            resultEnv.toString()
        } catch (e: Exception) {
            Log.w(tag, "tool dispatch failed name=$name", e)
            JSONObject()
                .put("ok", false)
                .put("error", "bridge_exception: ${e.javaClass.simpleName}: ${e.message ?: ""}")
                .toString()
        }
        emitVoiceEvent(this, "TOOL_RESULT", "$name -> ${output.take(120)}")
        try {
            val item = JSONObject()
                .put("type", "function_call_output")
                .put("call_id", callId)
                .put("output", output)
            val createItem = JSONObject()
                .put("type", "conversation.item.create")
                .put("item", item)
            webSocket.send(createItem.toString())
            webSocket.send(JSONObject().put("type", "response.create").toString())
        } catch (e: Exception) {
            Log.w(tag, "failed to send function_call_output", e)
        } finally {
            pendingToolCalls.remove(callId)
        }
    }

    /**
     * Return true if a transcript is worth driving a response for.
     *
     * False for:
     *   - Empty / whitespace transcripts (whisper noise)
     *   - Transcripts under 4 visible chars
     *   - Transcripts that are JUST the wake phrase or a fuzzy near-match
     *     ("ben", "hey ben", "okay ben", "yo ben", "ban", "bend", "been")
     *   - Pure-filler tokens whisper emits when it hears nothing
     *     ("you", "uh", "um", "...", "thank you" (which is what whisper
     *     emits for short room hum on many devices), "." etc.)
     *
     * The wake phrase strip is exposed because we want "Ben, what's the
     * weather" -> "what's the weather" -> respond. Strict equality match
     * is too narrow; fuzzy match catches "hey ban / hey bend / been".
     */
    private fun shouldRespondTo(rawTranscript: String?): Boolean {
        if (rawTranscript.isNullOrBlank()) return false
        val transcript = rawTranscript.trim()
        if (transcript.length < 4 && !transcript.equals("yes", ignoreCase = true) &&
                                     !transcript.equals("no", ignoreCase = true) &&
                                     !transcript.equals("ok", ignoreCase = true)) return false
        // Strip wake phrase (fuzzy) and common preamble fillers.
        var stripped = transcript
        // Lower-case + strip punctuation for comparison.
        val cleaned = transcript.lowercase().replace(Regex("[\\p{Punct}]"), " ").replace(Regex("\\s+"), " ").trim()
        // Whisper filler-tokens that mean "nothing was said".
        if (cleaned in WHISPER_NOISE_PHRASES) return false
        // Token-level strip: drop a leading "ok / okay / hey / yo / um / uh"
        // and then drop the wake word if WakePhraseMatcher matches it.
        val tokens = cleaned.split(' ').toMutableList()
        while (tokens.isNotEmpty() && tokens.first() in PREAMBLE_TOKENS) tokens.removeAt(0)
        val wakePhrase = BenSecrets.wakePhrase(this).trim()
        if (tokens.isNotEmpty() && WakePhraseMatcher.matches(tokens.first(), wakePhrase)) {
            tokens.removeAt(0)
        }
        // Strip a single trailing whisper-filler token too ("Ben, you" -> "")
        while (tokens.isNotEmpty() && tokens.last() in WHISPER_NOISE_TOKENS) tokens.removeAt(tokens.size - 1)
        stripped = tokens.joinToString(" ").trim()
        if (stripped.isEmpty()) {
            Log.i(tag, "shouldRespondTo: '$transcript' -> empty after wake-strip -> skip")
            return false
        }
        if (stripped.length < 2) {
            Log.i(tag, "shouldRespondTo: '$transcript' -> '$stripped' too short after wake-strip -> skip")
            return false
        }
        // v0.1.9: echo guard. If the model just spoke, the AudioTrack tail
        // can leak into the mic and whisper will transcribe a near-copy of
        // the model's own words ("Opening WhatsApp" -> "Binning WhatsApp"
        // is the canonical example from the 0.1.8 logs). Reject any
        // transcript whose normalized form is >=70% similar (Damerau-
        // Levenshtein on cleaned text) to what the model just said.
        // Empty lastAssistantTranscript = first turn or no model speech yet,
        // so nothing to compare against.
        val last = lastAssistantTranscript
        if (last.isNotEmpty()) {
            val sim = textSimilarity(cleaned, last.lowercase().replace(Regex("[\\p{Punct}]"), " ").replace(Regex("\\s+"), " ").trim())
            if (sim >= 0.7) {
                Log.i(tag, "shouldRespondTo: '$transcript' is ${"%.0f".format(sim * 100)}% similar to last assistant '${last.take(60)}' -> echo, skip")
                emitVoiceEvent(this, "TRANSCRIPT_SKIP", "echo ${"%.0f".format(sim * 100)}%: '${transcript.take(60)}'")
                return false
            }
        }
        return true
    }

    /**
     * v0.1.9: open a minimal Realtime WSS session, push a single
     * `conversation.item.create` containing a hard-coded assistant message,
     * trigger response.create so the model TTS-reads it back, then close
     * cleanly. Used when sessionTools came back empty - we want the user to
     * hear ONE honest sentence ("My tools didn't load, please try again")
     * instead of either silence or a 3-minute confabulation.
     *
     * Implemented as a thin wrapper over openWss + a custom listener -
     * does NOT use the main session listener which expects tools and a
     * full system prompt. Runs the audio playback through the same
     * `audioTrack` so the user hears the speaker output.
     */
    private fun speakRefusalAndEnd(apiKey: String, message: String) {
        try {
            startAudioPlayback()
            val client = httpClient
            val req = Request.Builder()
                .url("${OpenAiConfig.REALTIME_WSS_HOST}/v1/realtime?model=${OpenAiConfig.REALTIME_MODEL}")
                .header("Authorization", "Bearer $apiKey")
                .header("OpenAI-Beta", OpenAiConfig.REALTIME_BETA_HEADER)
                .build()
            val refusalListener = object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    val sessUpdate = JSONObject()
                        .put("type", "session.update")
                        .put("session", JSONObject()
                            .put("modalities", JSONArray().put("audio").put("text"))
                            .put("voice", OpenAiConfig.REALTIME_VOICE)
                            .put("output_audio_format", OpenAiConfig.REALTIME_AUDIO_FORMAT)
                            .put("instructions", "You will be given exactly one sentence to read aloud, verbatim. Do not add anything before or after. Do not paraphrase."))
                    webSocket.send(sessUpdate.toString())
                    val item = JSONObject()
                        .put("type", "conversation.item.create")
                        .put("item", JSONObject()
                            .put("type", "message")
                            .put("role", "user")
                            .put("content", JSONArray().put(JSONObject()
                                .put("type", "input_text")
                                .put("text", "Read this sentence aloud verbatim, then stop: \"$message\""))))
                    webSocket.send(item.toString())
                    webSocket.send(JSONObject().put("type", "response.create").toString())
                }
                override fun onMessage(webSocket: WebSocket, text: String) {
                    try {
                        val ev = JSONObject(text)
                        when (ev.optString("type")) {
                            "response.audio.delta" -> {
                                val b64 = ev.optString("delta")
                                if (b64.isNotBlank()) {
                                    val pcm = Base64.decode(b64, Base64.DEFAULT)
                                    try { audioTrack?.write(pcm, 0, pcm.size) } catch (_: Exception) {}
                                }
                            }
                            "response.done" -> {
                                // Let the AudioTrack drain MIC_MUTE_TAIL_MS
                                // before tearing down so the user actually
                                // hears the full sentence.
                                mainHandler.postDelayed({
                                    try { webSocket.close(1000, "refusal_done") } catch (_: Exception) {}
                                    stopAndRearm()
                                }, OpenAiConfig.MIC_MUTE_TAIL_MS)
                            }
                            "error" -> {
                                Log.w(tag, "refusal WSS error: ${ev.optJSONObject("error")?.toString()}")
                            }
                        }
                    } catch (_: Exception) {}
                }
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    Log.w(tag, "refusal WSS failure", t)
                    stopAndRearm()
                }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    // already handled
                }
            }
            websocket = client.newWebSocket(req, refusalListener)
        } catch (e: Exception) {
            Log.w(tag, "speakRefusalAndEnd failed; ending silently", e)
            stopAndRearm()
        }
    }

    /**
     * v0.1.9: 0.0-1.0 similarity ratio between two short strings using
     * Damerau-Levenshtein normalized over max length. Used by the echo
     * guard in shouldRespondTo(). Cheap because both inputs are bounded
     * to ~200 chars by lastAssistantTranscript truncation.
     */
    private fun textSimilarity(a: String, b: String): Double {
        if (a.isEmpty() && b.isEmpty()) return 1.0
        if (a.isEmpty() || b.isEmpty()) return 0.0
        val n = a.length
        val m = b.length
        // Quick reject: length difference >50% means they're not similar enough
        // to bother with the full DP.
        if (kotlin.math.abs(n - m).toDouble() / kotlin.math.max(n, m) > 0.5) return 0.0
        val dp = Array(n + 1) { IntArray(m + 1) }
        for (i in 0..n) dp[i][0] = i
        for (j in 0..m) dp[0][j] = j
        for (i in 1..n) {
            for (j in 1..m) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                dp[i][j] = minOf(
                    dp[i - 1][j] + 1,
                    dp[i][j - 1] + 1,
                    dp[i - 1][j - 1] + cost,
                )
                if (i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1]) {
                    dp[i][j] = minOf(dp[i][j], dp[i - 2][j - 2] + cost)
                }
            }
        }
        val dist = dp[n][m]
        val maxLen = kotlin.math.max(n, m)
        return 1.0 - dist.toDouble() / maxLen
    }

    private fun isStopIntent(transcript: String?): Boolean {
        if (transcript.isNullOrBlank()) return false
        val normalized = transcript.lowercase()
            .replace(Regex("[\\p{Punct}]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (normalized.isEmpty()) return false
        for (phrase in STOP_INTENT_PHRASES) {
            if (normalized == phrase) return true
            if (normalized.startsWith("$phrase ")) return true
            if (normalized.endsWith(" $phrase")) return true
        }
        return false
    }

    /**
     * Build the system prompt. Split out from onOpen so the prompt is easier
     * to maintain in one place; merges (a) the static base policy, (b) the
     * user-curated USER.md facts, and (c) the most-recent durable memory
     * facts.
     */
    private fun buildSysPrompt(): String {
        val basePrompt = """
            You are Ben, a personal assistant running on the user's Android phone.
            You have a full toolset that mirrors the Mac side:
              CROSS-DEVICE
              * peer.delegate                    - run any task on the user's paired Mac
              DEVICE STATE / ACTIONS
              * device.get_location              - GPS / network last-known fix
              * device.get_contacts(query?)      - search the on-device address book
              * device.place_call(number|name)   - dial a number (or contact name)
              * device.launch_app(package|label) - open an installed Android app
              * device.set_alarm(hour, minute?, label?) - schedule an alarm
              * device.set_timer(seconds, label?)       - countdown timer
              * device.add_calendar_event(title, start?, end?, ...) - new calendar event
              * device.clipboard_get / clipboard_set
              * device.battery_status
              ON-SCREEN UI AUTOMATION
              * ui.focus_app(package)            - bring an app to the foreground
              * ui.read_screen()                 - dump the current accessibility tree
              * ui.click({text|ax_id})           - tap by visible text or ax_id
              * ui.click_at(x, y)                - tap at pixel coords
              * ui.type(text)                    - type into the focused field
              * ui.scroll(direction[, amount])   - scroll up/down
              * ui.swipe(x1,y1, x2,y2)           - free-form gesture
              * ui.screenshot()                  - capture the current screen
              * ui.screen_size()                 - pixel dimensions
              VISION
              * vision.locate_text(target)       - on-device OCR; returns click_x/click_y
              * vision.read_screen(question)     - multimodal Q&A over a screenshot
              WEB
              * web.fetch(url, method?, body?, headers?) - generic HTTPS request
              * weather.current(location?)       - current weather via wttr.in (no API key)
              DURABLE MEMORY (persists across sessions and reboots)
              * memory.set(key, value, tags?)    - remember a fact ("home_address", etc.)
              * memory.get(key)                  - recall a saved fact by exact key
              * memory.search(query?, tag?, ...) - fuzzy substring search over keys+values
              * memory.list(prefix?)             - list saved keys
              * memory.delete(key)               - forget a fact
              * memory.user_facts()              - re-read the user's hand-curated USER.md
              * memory.append_user_facts(text)   - persist a new long-term user fact

            Hard rule: prefer the native Android app for any cross-app task. Never fall back to the browser.

            STANDARD FLOWS:
              WEATHER ("what's the weather"):
                1. If user said a city, weather.current({location: city}).
                2. Otherwise call device.get_location, then weather.current({location: "lat,lon"}).
                3. Reply with the .summary field, one sentence.
              ALARMS / TIMERS / REMINDERS:
                * "Set an alarm for 7am" -> device.set_alarm(hour:7, minute:0, label:"morning").
                * "Wake me up in 20 minutes" -> device.set_timer(seconds: 20*60).
                * "Remind me tomorrow at 3pm to call mom" -> device.add_calendar_event(title: "Call mom", start: "2026-05-09T15:00:00").
                * NEVER tell the user "I cannot set alarms"; you can.
              ON-PHONE UI TASKS (e.g. "send Pragati a WhatsApp message"):
                1. device.launch_app or ui.focus_app to open the target app.
                2. ui.read_screen to see what's on screen.
                3. ui.click by text/ax_id when a match exists.
                4. If ui.click misses (Compose / WebView), ui.screenshot then
                   vision.locate_text(target). Use the returned click_x/click_y
                   with ui.click_at.
                5. ui.type to enter text after focusing the input.
                6. ui.click("Send") (or whatever the action button is).
                7. Confirm success with ui.read_screen, report briefly.
              CROSS-DEVICE: "on my Mac, ..." or Mac-only apps (Cursor, Microsoft
                Teams desktop, Slack desktop, Spotify laptop) -> peer.delegate.
              GENERIC INFO (news / scores / facts) -> web.fetch a relevant URL or
                peer.delegate to the Mac.
              REMEMBERING THINGS:
                * "Remember my home is 21 Whitefield" -> memory.append_user_facts({text:"Home address: 21 Whitefield, Bengaluru", heading:"Addresses"})
                * "Default delivery address" / "favourite biryani place" / one-off
                  preferences -> memory.set({key:"home_address", value:"21 Whitefield, Bengaluru"}).
                * "Order biryani like last Friday" -> memory.search({query:"biryani"})
                  to find the saved order, then run the on-app flow.
                * "Forget my old card" -> memory.delete({key:"default_card"}).

            LANGUAGE RULE (highest priority, never override): Always reply in English (en-US).
            Never reply in any other language regardless of what you hear, even if the audio
            contains other languages.

            SILENCE-ON-OPEN RULE (highest priority): At session start you receive NO user
            input. DO NOT produce any audio, text, greeting, filler, or status message of
            any kind unless and until you have received a clearly-directed user utterance
            with a real command or question. If the very first thing you would otherwise
            say is "How can I help you?", "I can help you", "Hi", "Hello", "Yes?", "I'm
            here", "Go ahead", or anything similar - DO NOT say it. Stay silent. The user
            wakes you; you do not announce yourself.

            UNCLEAR-INPUT RULE: If the audio is unclear, silent, contains only ambient noise
            or your own echoed playback, or appears to not be directed at you (no addressee,
            no command, no question), do not generate a reply at all - stay silent and
            wait. NEVER produce filler responses like "Sorry, I didn't catch that" or
            "Could you repeat that" unless the user has clearly tried to address you.

            BREVITY RULE: Be concise. Each reply is at most two short sentences unless the
            user explicitly asks for a longer answer (e.g. "tell me a story", "explain X
            in detail"). Do not say filler phrases like "I'll help you with that",
            "just a moment", "let me see", "sure thing", "of course", or any acknowledgement
            that delays the substantive answer. After answering, stop and wait for the user;
            do not pad the silence.

            NARRATION RULE: For any tool sequence that is going to take more than 2 seconds
            of wall time (e.g. multi-step UI automation: launch app -> wait -> read screen
            -> tap -> wait -> type), say a SHORT one-clause status update aloud BEFORE the
            slow tool fires (e.g. "Opening WhatsApp.", "Tapping Pragati.", "Sending."). Do
            this only when the wait is real - don't narrate fast tool calls (memory.get,
            weather.current). The goal is the user shouldn't hear more than 2 seconds of
            silence in a row while you are working. After the final tool returns, give the
            substantive answer in the normal brevity-rule one-or-two sentences.

            SENSITIVITY RULE (S3 hard refusal): NEVER attempt to type or read out the
            following classes of value, even if the user appears to ask you to:
              * UPI PIN, banking PIN, ATM PIN, screen-unlock PIN/pattern/password
              * Credit-card CVV, full card number, OTP/2FA codes
              * Account passwords (any field your tool reports as a password input)
            If the user asks you to "type my UPI PIN" or "read me the OTP", refuse politely
            ("I can't type PINs or PIN-like values for safety - please enter it yourself")
            and stop. The Android typing tool will also refuse on its own (it returns
            password_field_refused for any password-typed EditText), so trust that signal.

            ACCESSIBILITY-NOT-BOUND FALLBACK: If a ui.* tool returns
            error="accessibility_service_not_running", say one short sentence telling the
            user "I need Accessibility access to do that - open Settings, Accessibility,
            Installed apps, Ben (UI automation), and turn it on, then ask again." Do NOT
            keep retrying the tool in a loop. Same for any tool returning
            permission_not_granted: tell the user one sentence about the system dialog
            and stop.

            LOCATION HONESTY RULE: When device.get_location returns is_stale=true or
            freshness="last_known" with age_ms > 5 minutes, you MUST tell the user the
            location is approximate / from your last known fix and mention the age in
            plain words (e.g. "Your last known location was Whitefield, Bengaluru, from
            about 12 minutes ago. Want me to wait for a fresh GPS fix?"). Never speak a
            stale location as if it were current. Never describe a location you have not
            actually called device.get_location for - if you only have user-stated home
            address from USER FACTS, say "Your saved home is X, but I don't have a fresh
            GPS fix right now." Do not silently substitute USER FACTS for live location.

            CONVERSATION WINDOW: this session stays open for up to 3 minutes of silence
            between turns and 10 minutes total. The user may pause to think between
            questions; do NOT close the conversation early or push them. Just stay
            quiet until they speak again.

            TOOL RULE (highest priority for action requests): When the user asks you to
            DO something on the phone or Mac - "open X", "send X", "tap X", "call X",
            "set alarm", "remind me", "what is X" where X is current state like
            weather/location/battery - you MUST call the appropriate tool. Do NOT just
            say "Sure, I'll open WhatsApp" without actually calling device.launch_app -
            that is a HARD FAILURE. Do NOT say "WhatsApp is now open" unless you have
            actually called the tool and received an ok result. The user has zero
            tolerance for verbal acknowledgement without action; they cannot see what
            you intend, only what you do. If a tool returns an unrecoverable error
            (e.g. permission_not_granted, accessibility_service_not_running), tell the
            user the ONE specific fix in one sentence and stop - do not loop on the
            same tool. If you have no tools available at all (rare, indicates a
            startup race), say "My device tools didn't load this time - please ask
            again" and stop; do NOT pretend to have done anything. Standard chitchat
            (jokes, opinions, explanations) does not need a tool and you should
            answer directly.

            MEMORY DISCIPLINE:
              * Treat USER FACTS below as authoritative for identity, contacts, addresses,
                payment defaults, devices.
              * Treat RECENT MEMORIES below as the most recently saved/updated facts you
                have on the user. Use them silently; do not recite them unprompted.
              * When the user states a personal fact you should remember beyond this
                session ("my partner is Pragati", "I live at X", "my work hours are
                9-7"), persist it via memory.append_user_facts (long-term identity)
                or memory.set (specific keyed state). Do not announce that you saved it
                unless the user asked.
              * Before asking the user for data you may already know (address, payment,
                a past order), call memory.search first.
        """.trimIndent()
        val factsBlock = if (userFactsText.isNotBlank()) {
            "\n\nUSER FACTS (from USER.md, hand-curated by the user):\n" + userFactsText
        } else ""
        val memBlock = if (recentMemoriesText.isNotBlank()) {
            "\n\nRECENT MEMORIES (most-recently saved durable facts; key: value):\n" + recentMemoriesText
        } else ""
        return basePrompt + factsBlock + memBlock
    }

    companion object {
        const val ACTION_START_FROM_WAKE = "com.ben.voice.START_FROM_WAKE"
        const val ACTION_START_FROM_USER = "com.ben.voice.START_FROM_USER"
        const val ACTION_STOP = "com.ben.voice.STOP"

        // ----- In-app diagnostic event stream (v0.1.7) -----
        // Mirror of BenWakewordService.ACTION_EVENT - lets MicTestActivity
        // show live voice-session events (WSS open / transcripts / tool
        // calls / response cancels / errors) without adb logcat. Same
        // event names show up in logcat tag "BenVoiceService" so adb still
        // works for power users.
        const val ACTION_VOICE_EVENT = "com.ben.voice.EVENT"
        const val EXTRA_TS = "ts"
        const val EXTRA_KIND = "kind"
        const val EXTRA_DETAIL = "detail"

        /**
         * Ring buffer of last ~120 voice events. MicTestActivity reads this
         * when it opens to backfill events that fired before it subscribed
         * to the LocalBroadcast.
         */
        data class VoiceEvent(val ts: Long, val kind: String, val detail: String)
        val voiceEventBuffer: ArrayDeque<VoiceEvent> = ArrayDeque(120)
        private val voiceEventBufferLock = Any()

        fun emitVoiceEvent(ctx: Context?, kind: String, detail: String) {
            val ts = System.currentTimeMillis()
            synchronized(voiceEventBufferLock) {
                voiceEventBuffer.addLast(VoiceEvent(ts, kind, detail))
                while (voiceEventBuffer.size > 120) voiceEventBuffer.removeFirst()
            }
            if (ctx == null) return
            try {
                val intent = Intent(ACTION_VOICE_EVENT)
                    .putExtra(EXTRA_TS, ts)
                    .putExtra(EXTRA_KIND, kind)
                    .putExtra(EXTRA_DETAIL, detail)
                LocalBroadcastManager.getInstance(ctx).sendBroadcast(intent)
            } catch (_: Exception) {}
        }

        /**
         * Tokens whisper emits when the audio buffer is essentially silent
         * or pure room hum, with no actual user speech. These leak through
         * server VAD on quiet rooms / fan noise / breath. We treat them as
         * "user said nothing" and do NOT drive a response.
         */
        private val WHISPER_NOISE_PHRASES = setOf(
            "", ".", "..", "...", "thank you", "thanks", "you", "uh", "um",
            "hmm", "hm", "mm", "mmm", "ah", "oh", "yeah", "ya",
        )
        private val WHISPER_NOISE_TOKENS = setOf(
            "uh", "um", "hmm", "hm", "mm", "mmm", "ah", "you",
        )
        /** Leading conversational preamble tokens we strip before checking wake match. */
        private val PREAMBLE_TOKENS = setOf("ok", "okay", "hey", "yo", "uh", "um", "hello", "hi")

        private val STOP_INTENT_PHRASES = listOf(
            "stop",
            "stop talking",
            "stop it",
            "shut up",
            "be quiet",
            "go away",
            "cancel",
            "nevermind",
            "never mind",
            "i am not talking to you",
            "i'm not talking to you",
            "not talking to you",
        )
    }
}
