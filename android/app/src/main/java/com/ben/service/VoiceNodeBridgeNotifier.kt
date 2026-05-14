package com.ben.service

import android.content.Context
import android.util.Log
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.Socket
import org.json.JSONObject

/**
 * Outbound JSON-RPC client to the embedded Node bridge for session lifecycle events.
 * Mirrors the bridge in NodeBridgeService.kt - we send notifications, the Node side
 * keeps the canonical SessionTimer + JSONL writer.
 */
object VoiceNodeBridgeNotifier {
    private const val TAG = "VoiceNodeBridgeNotifier"

    fun notifySessionStarted(ctx: Context, sessionId: String) {
        // v0.1.9: emit an in-app diagnostic so MicTest shows whether the
        // history writer accepted the session-started notification. Pre-fix
        // the user reported "history not being saved" but had no way to
        // tell whether the Node bridge was reachable - now this surfaces
        // it explicitly. Failure here means HistoryActivity will be empty
        // for this session.
        sendWithAck(ctx, "session.started", JSONObject().put("session_id", sessionId).put("device", "phone")) { ok, err ->
            BenVoiceService.emitVoiceEvent(
                ctx,
                if (ok) "HISTORY_WRITE_OK" else "HISTORY_WRITE_FAILED",
                if (ok) "session.started recorded" else "session.started failed: ${err ?: "unknown"}",
            )
        }
    }

    fun notifySessionEnded(ctx: Context, sessionId: String) {
        send(ctx, "session.ended", JSONObject().put("session_id", sessionId).put("reason", "stop"))
    }

    fun markActivity(ctx: Context, reason: String) {
        send(ctx, "session.activity", JSONObject().put("reason", reason))
    }

    fun recordUserText(ctx: Context, sessionId: String, text: String) {
        send(ctx, "session.user_text", JSONObject().put("session_id", sessionId).put("text", text))
    }

    fun recordAssistantText(ctx: Context, sessionId: String, text: String) {
        send(ctx, "session.assistant_text", JSONObject().put("session_id", sessionId).put("text", text))
    }

    private fun send(@Suppress("UNUSED_PARAMETER") ctx: Context, method: String, params: JSONObject) {
        sendWithAck(ctx, method, params, null)
    }

    /**
     * v0.1.9: send with an optional callback that receives (ok, errMsg).
     * Used by notifySessionStarted to surface the result to MicTest. The
     * existing best-effort caller pattern (no ack) is preserved by passing
     * `cb=null`.
     */
    private fun sendWithAck(
        @Suppress("UNUSED_PARAMETER") ctx: Context,
        method: String,
        params: JSONObject,
        cb: ((Boolean, String?) -> Unit)?,
    ) {
        Thread {
            var ok = false
            var err: String? = null
            try {
                // v0.1.9: bumped soTimeout from 250ms to 800ms because right
                // after wake the embedded Node bridge can still be flushing
                // its boot tasks (first wake after a cold app launch). 250ms
                // was empirically not enough to receive the response back,
                // so the writer wrote the session but we logged failure and
                // surfaced it to the user as "history broken".
                Socket(InetAddress.getByName("127.0.0.1"), 18792).use { s ->
                    s.soTimeout = 800
                    val out = OutputStreamWriter(s.getOutputStream())
                    val inn = BufferedReader(InputStreamReader(s.getInputStream()))
                    val req = JSONObject().put("id", "n").put("method", method).put("params", params)
                    out.write(req.toString()); out.write("\n"); out.flush()
                    val line = inn.readLine()
                    if (line != null) {
                        val resp = try { JSONObject(line) } catch (_: Exception) { null }
                        if (resp != null && !resp.has("error")) ok = true
                        else if (resp != null) err = resp.optString("error", "rpc_error")
                        else err = "unparseable_response"
                    } else {
                        err = "no_response"
                    }
                }
            } catch (e: Exception) {
                err = "${e.javaClass.simpleName}:${e.message ?: ""}"
                Log.v(TAG, "notify $method failed: ${e.message}")
            }
            cb?.invoke(ok, err)
        }.start()
    }
}
