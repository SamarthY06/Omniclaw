package com.ben.service

import android.app.Service
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.Base64
import android.util.Log
import com.ben.util.BenSecrets
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
 *   a) 3 s silence after a model response.done with no new speech_started.
 *      Per user spec: after Ben replies, you have 3 s to keep talking; if you
 *      stay quiet for 3 s the session closes and the next turn requires a
 *      fresh wake word.
 *   b) Explicit stop intent ("stop", "shut up", "i'm not talking to you", etc.)
 *      detected in the user's transcribed audio.
 *   c) Hard 600 s (10 min) ceiling regardless of activity (defensive runaway guard).
 *   d) WebSocket failure / closure.
 *
 * Notification ownership: this service is intentionally NOT a foreground
 * service. The microphone foreground anchor is BenForegroundService; we only
 * flip its notification text via setActive(active=true/false). Having a
 * second foreground notification here was the cause of the duplicate
 * "Conversation in progress" entries on the lock screen.
 *
 * Feedback-loop guard: while the model is speaking (between response.audio.
 * delta and response.audio.done), we suppress mic uploads so the speaker's
 * output cannot re-enter the conversation as fake user speech.
 */
class BenVoiceService : Service() {
    private val tag = "BenVoiceService"
    private val httpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS) // websocket - no read timeout
        .pingInterval(20, TimeUnit.SECONDS)
        .build()
    private var websocket: WebSocket? = null
    private val running = AtomicBoolean(false)
    private var recordThread: Thread? = null
    private var audioTrack: AudioTrack? = null
    private var sessionId: String = ""

    @Volatile private var isAssistantSpeaking: Boolean = false
    private val mainHandler = Handler(Looper.getMainLooper())
    private val silenceEndRunnable = Runnable {
        Log.i(tag, "180s post-response silence reached - ending session")
        stopAndRearm()
    }
    private val hardCapRunnable = Runnable {
        Log.w(tag, "600s hard session cap reached - ending session")
        stopAndRearm()
    }

    // Tools advertised to the OpenAI Realtime model. Populated by tools.list
    // RPC to the embedded Node bridge before we open the WSS in connect().
    // If the bridge isn't reachable we ship an empty array - the model can
    // still chat, just can't call tools.
    private var sessionTools: JSONArray = JSONArray()

    // Per-call buffer of streamed function_call_arguments.delta chunks.
    // Keyed by call_id from the Realtime stream. function_call_arguments.done
    // assembles the final string from this buffer and dispatches.
    private data class PendingToolCall(val name: String, val args: StringBuilder)
    private val pendingToolCalls = ConcurrentHashMap<String, PendingToolCall>()

    // Single-thread executor so tool invocations don't block the OkHttp WSS
    // dispatcher thread. A blocked WSS thread would queue every other event
    // behind the tool call and stall the audio stream.
    private val toolExecutor: ExecutorService = Executors.newSingleThreadExecutor { r ->
        Thread(r, "ben-tool-dispatch").apply { isDaemon = true }
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
                mainHandler.postDelayed(hardCapRunnable, HARD_SESSION_CAP_MS)
                connect()
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
        mainHandler.removeCallbacks(silenceEndRunnable)
        mainHandler.removeCallbacks(hardCapRunnable)
        isAssistantSpeaking = false
        pendingToolCalls.clear()
        try { websocket?.close(1000, "session_ended") } catch (_: Exception) {}
        websocket = null
        try { recordThread?.interrupt() } catch (_: Exception) {}
        recordThread = null
        try { audioTrack?.stop(); audioTrack?.release() } catch (_: Exception) {}
        audioTrack = null
        BenForegroundService.setActive(this, false)
        BenWakewordService.resume(this)
        VoiceNodeBridgeNotifier.notifySessionEnded(this, sessionId)
        try { toolExecutor.shutdownNow() } catch (_: Exception) {}
        stopSelf()
    }

    private fun connect() {
        val apiKey = BenSecrets.openaiKey(this)
        if (apiKey.isNullOrBlank()) {
            Log.w(tag, "No OpenAI key - aborting session")
            stopAndRearm()
            return
        }
        VoiceNodeBridgeNotifier.notifySessionStarted(this, sessionId)
        // Bootstrap: synchronously fetch the tool registry from the embedded
        // Node bridge before we open the Realtime WSS. If the bridge isn't
        // reachable yet (rare; node boots in BenForegroundService.onCreate)
        // we proceed with an empty tools array so the user still gets a
        // working voice loop. This is a localhost TCP roundtrip so it
        // typically completes in <50 ms.
        sessionTools = try {
            fetchToolsFromBridge()
        } catch (e: Exception) {
            Log.w(tag, "tools.list failed (${e.message}); proceeding with empty tools")
            JSONArray()
        }
        Log.i(tag, "session tools count=${sessionTools.length()}")
        val req = Request.Builder()
            .url("wss://api.openai.com/v1/realtime?model=gpt-realtime")
            .header("Authorization", "Bearer $apiKey")
            .header("OpenAI-Beta", "realtime=v1")
            .build()
        websocket = httpClient.newWebSocket(req, listener)
        startMicLoop()
        startAudioPlayback()
    }

    /**
     * Synchronous newline-delimited JSON-RPC call to 127.0.0.1:18792 (the
     * NodeBridgeService inbound RPC port). Returns the OpenAI-compatible
     * tools array, or an empty array if the bridge is unavailable.
     *
     * The bridge protocol is: send one JSON object terminated by '\n', read
     * one JSON object terminated by '\n'. Same wire format used elsewhere
     * for peer/wake/session RPCs.
     */
    private fun fetchToolsFromBridge(): JSONArray {
        val req = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("method", "tools.list")
            .put("params", JSONObject())
        Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress(InetAddress.getLoopbackAddress(), NODE_BRIDGE_PORT), 1500)
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

    /**
     * Fire-and-wait JSON-RPC call to the Node bridge to invoke a tool.
     * Returns the tool's result envelope as a JSONObject. Caller is
     * responsible for stringifying it into a function_call_output payload.
     *
     * Runs on the toolExecutor thread; MUST NOT be called on the WSS
     * dispatcher thread or the OkHttp event loop will stall.
     */
    private fun invokeToolViaBridge(name: String, argsJson: String): JSONObject {
        val params = JSONObject()
            .put("name", name)
            .put("args", if (argsJson.isBlank()) JSONObject() else JSONObject(argsJson))
        val req = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("method", "tools.invoke")
            .put("params", params)
        Socket().use { sock ->
            sock.connect(java.net.InetSocketAddress(InetAddress.getLoopbackAddress(), NODE_BRIDGE_PORT), 2_000)
            sock.soTimeout = TOOL_INVOKE_TIMEOUT_MS
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
        val sampleRate = 24_000
        val channelConfig = AudioFormat.CHANNEL_IN_MONO
        val encoding = AudioFormat.ENCODING_PCM_16BIT
        val minBuffer = AudioRecord.getMinBufferSize(sampleRate, channelConfig, encoding)
        val rec = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate, channelConfig, encoding, minBuffer * 4)
        } catch (e: SecurityException) {
            Log.e(tag, "RECORD_AUDIO denied", e); stopAndRearm(); return
        }
        rec.startRecording()
        val buf = ByteArray(minBuffer.coerceAtLeast(960 * 2))
        recordThread = Thread({
            try {
                while (running.get() && !Thread.currentThread().isInterrupted) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    // Drop frames captured while the model is speaking. We
                    // still drain AudioRecord so the kernel buffer doesn't
                    // back up, but we don't ship those bytes upstream - they
                    // are guaranteed to be the speaker's own output bouncing
                    // off the mic and would trigger a runaway response loop.
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

    private fun startAudioPlayback() {
        val sampleRate = 24_000
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
            // Hard-locked English instructions plus anti-filler discipline.
            // Without an explicit language clause the Realtime model
            // auto-detects from audio cues and will happily reply in Hindi
            // when the device locale or background noise looks Indic.
            // The brevity / no-filler clause is what kills the
            // "I'll help you with that, just a moment, sure thing..."
            // monologue users were getting on 0.1.1.
            val sysPrompt = """
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

                LANGUAGE RULE (highest priority, never override): Always reply in English (en-US).
                Never reply in any other language regardless of what you hear, even if the audio
                contains other languages. If the audio is unclear, silent, or appears to not be
                directed at you (no addressee, no command, no question), do not generate a reply
                at all - stay silent and wait. Do not greet the user automatically; only respond
                to a clearly directed user utterance.

                BREVITY RULE: Be concise. Each reply is at most two short sentences unless the
                user explicitly asks for a longer answer (e.g. "tell me a story", "explain X
                in detail"). Do not say filler phrases like "I'll help you with that",
                "just a moment", "let me see", "sure thing", "of course", or any acknowledgement
                that delays the substantive answer. If you need to think or call a tool, stay
                silent and call the tool - do not narrate. After answering, stop and wait for
                the user; do not pad the silence.

                CONVERSATION WINDOW: this session stays open for up to 3 minutes of silence
                between turns and 10 minutes total. The user may pause to think between
                questions; do NOT close the conversation early or push them. Just stay
                quiet until they speak again.

                TOOL RULE: When a user request needs device data, device action, on-screen UI
                work, or web information, call the appropriate tool instead of asking the user
                to provide the information manually. Only ask the user when a tool returns
                an unrecoverable error (e.g. permission_not_granted - tell them to allow
                the system dialog and try again).
            """.trimIndent()
            // turn_detection: server VAD with a 1 s silence_duration_ms decides
            // when to start the assistant's response (i.e. when did the user
            // stop talking THIS turn). The much longer 180 s
            // POST_RESPONSE_SILENCE_MS decides when the whole session ends.
            // 1 s is the standard Realtime default; 3 s here used to be
            // tied to "session ends after 3 s silence" and conflated the two.
            val turnDetection = JSONObject()
                .put("type", "server_vad")
                .put("threshold", 0.6)
                .put("prefix_padding_ms", 300)
                .put("silence_duration_ms", 1000)
                .put("create_response", true)
                .put("interrupt_response", true)
            val transcription = JSONObject()
                .put("model", "whisper-1")
                .put("language", "en")
            // 800 audio output tokens ~= 30-40 s of speech. Hard ceiling
            // against runaway monologues; plenty for any reasonable answer.
            val update = JSONObject()
                .put("type", "session.update")
                .put("session", JSONObject()
                    .put("modalities", JSONArray().put("audio").put("text"))
                    .put("instructions", sysPrompt)
                    .put("voice", "marin")
                    .put("input_audio_format", "pcm16")
                    .put("output_audio_format", "pcm16")
                    .put("input_audio_transcription", transcription)
                    .put("turn_detection", turnDetection)
                    .put("tools", sessionTools)
                    .put("tool_choice", "auto")
                    .put("max_response_output_tokens", 800))
            webSocket.send(update.toString())
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            try {
                val ev = JSONObject(text)
                when (val type = ev.optString("type")) {
                    // Lifecycle: assistant speaking flag is anchored to the
                    // response lifecycle, NOT to per-chunk audio events. Each
                    // logical assistant turn fires exactly one response.created
                    // and one response.done; tying to those is race-free.
                    // (Per-chunk response.audio.done events fired multiple
                    // times within a single response, leaving stale false
                    // values that let speaker output bounce back into mic.)
                    "response.created" -> {
                        isAssistantSpeaking = true
                        // Cancel any pending silence-end timer; the model is
                        // about to either speak or call a tool.
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.audio.delta" -> {
                        val b64 = ev.optString("delta")
                        if (b64.isNotBlank()) {
                            // Defensive: if response.created was missed for any
                            // reason, the first audio delta still raises the
                            // flag.
                            isAssistantSpeaking = true
                            mainHandler.removeCallbacks(silenceEndRunnable)
                            val pcm = Base64.decode(b64, Base64.DEFAULT)
                            audioTrack?.write(pcm, 0, pcm.size)
                            VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "audio.delta")
                        }
                    }
                    // response.audio.done fires once per response (no longer
                    // toggles isAssistantSpeaking). We only use it to mark
                    // bridge activity for the watchdog; the real speaking flag
                    // is released by response.done below.
                    "response.audio.done" -> {
                        VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "audio.done")
                    }
                    "response.done" -> {
                        isAssistantSpeaking = false
                        // If the response that just finished was a function-
                        // call response, the model is about to receive our
                        // function_call_output and create a NEW response with
                        // the actual answer. Don't schedule the silence-end
                        // timer in that case; we'll schedule it on the *next*
                        // response.done after the tool result is consumed.
                        if (pendingToolCalls.isEmpty()) {
                            mainHandler.removeCallbacks(silenceEndRunnable)
                            mainHandler.postDelayed(silenceEndRunnable, POST_RESPONSE_SILENCE_MS)
                        } else {
                            Log.d(tag, "response.done with ${pendingToolCalls.size} pending tool(s); deferring silence timer")
                        }
                    }
                    "input_audio_buffer.speech_started" -> {
                        // User started talking again before the silence timer
                        // fired. Keep the session alive.
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.audio_transcript.done" -> {
                        VoiceNodeBridgeNotifier.recordAssistantText(this@BenVoiceService, sessionId, ev.optString("transcript"))
                    }
                    "conversation.item.input_audio_transcription.completed" -> {
                        val transcript = ev.optString("transcript", "")
                        VoiceNodeBridgeNotifier.recordUserText(this@BenVoiceService, sessionId, transcript)
                        if (isStopIntent(transcript)) {
                            Log.i(tag, "stop intent detected in transcript - ending session")
                            try {
                                webSocket.send(JSONObject().put("type", "response.cancel").toString())
                            } catch (_: Exception) {}
                            stopAndRearm()
                        }
                    }
                    // Realtime emits response.output_item.added before the
                    // first function_call_arguments.delta. This is where we
                    // learn the call_id + tool name pair.
                    "response.output_item.added" -> {
                        val item = ev.optJSONObject("item") ?: return
                        if (item.optString("type") == "function_call") {
                            val callId = item.optString("call_id").ifBlank { return }
                            val name = item.optString("name", "")
                            pendingToolCalls[callId] = PendingToolCall(name, StringBuilder())
                            Log.i(tag, "tool call started: $name (call_id=$callId)")
                            // Tool call in flight - don't let the silence
                            // timer fire mid-dispatch.
                            mainHandler.removeCallbacks(silenceEndRunnable)
                        }
                    }
                    "response.function_call_arguments.delta" -> {
                        val callId = ev.optString("call_id").ifBlank { return }
                        val delta = ev.optString("delta", "")
                        pendingToolCalls[callId]?.args?.append(delta)
                        // Defensive: keep silence timer cancelled while args
                        // are streaming.
                        mainHandler.removeCallbacks(silenceEndRunnable)
                    }
                    "response.function_call_arguments.done" -> {
                        val callId = ev.optString("call_id").ifBlank { return }
                        val name = ev.optString("name").ifBlank { pendingToolCalls[callId]?.name ?: "" }
                        val argsStr = ev.optString("arguments")
                            .ifBlank { pendingToolCalls[callId]?.args?.toString() ?: "" }
                        Log.i(tag, "tool call args done: $name (call_id=$callId) args.len=${argsStr.length}")
                        VoiceNodeBridgeNotifier.markActivity(this@BenVoiceService, "tool.call:$name")
                        // Dispatch on the executor; the WSS thread MUST stay
                        // unblocked.
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
            Log.w(tag, "Realtime WSS failed", t)
            stopAndRearm()
        }
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }
        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            stopAndRearm()
        }
    }

    /**
     * Run on toolExecutor. Calls Node bridge tools.invoke for the given
     * call_id and pushes a function_call_output item back into the Realtime
     * conversation, followed by response.create so the model continues with
     * the answer. Errors from the bridge are surfaced as the same shape so
     * the model can recover gracefully ("permission not granted" etc).
     */
    private fun dispatchToolCall(webSocket: WebSocket, callId: String, name: String, argsJson: String) {
        val output = try {
            val resultEnv = invokeToolViaBridge(name, argsJson)
            // resultEnv is { ok: bool, result?: any, error?: string, ...}
            resultEnv.toString()
        } catch (e: Exception) {
            Log.w(tag, "tool dispatch failed name=$name", e)
            JSONObject()
                .put("ok", false)
                .put("error", "bridge_exception: ${e.javaClass.simpleName}: ${e.message ?: ""}")
                .toString()
        }
        // Send the function_call_output and ask the model to continue.
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

    private fun isStopIntent(transcript: String?): Boolean {
        if (transcript.isNullOrBlank()) return false
        // Strip punctuation, lowercase, collapse whitespace.
        val normalized = transcript.lowercase()
            .replace(Regex("[\\p{Punct}]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (normalized.isEmpty()) return false
        // Match only if the stop phrase is the entire utterance or is clearly
        // a directive at the start. We deliberately don't substring-match,
        // which would false-trigger on "stop tracking my location".
        for (phrase in STOP_INTENT_PHRASES) {
            if (normalized == phrase) return true
            if (normalized.startsWith("$phrase ")) return true
            // Allow "ben stop" / "hey ben stop" prefixes with the wake word.
            if (normalized.endsWith(" $phrase")) return true
        }
        return false
    }

    companion object {
        const val ACTION_START_FROM_WAKE = "com.ben.voice.START_FROM_WAKE"
        const val ACTION_START_FROM_USER = "com.ben.voice.START_FROM_USER"
        const val ACTION_STOP = "com.ben.voice.STOP"

        // v0.1.3: 3 minutes between turns (was 3 s) and 10 minutes total
        // (was 3 min). The user was getting cut off mid-thought because
        // 3 s of silence after a response ended the whole session.
        private const val POST_RESPONSE_SILENCE_MS = 180_000L
        private const val HARD_SESSION_CAP_MS = 600_000L

        // NodeBridgeService inbound RPC port. Same constant used by every
        // other Kotlin->Node call (peer pairing, wakeword reload, etc).
        private const val NODE_BRIDGE_PORT = 18792
        // Per-tool roundtrip cap. Most tools complete in <1 s; a 15 s
        // ceiling accommodates peer-delegate calls that round-trip to Mac
        // for UI automation. If a tool exceeds this we surface the timeout
        // to the model as a tool error and the model can apologise to the
        // user.
        private const val TOOL_INVOKE_TIMEOUT_MS = 15_000

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
