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
        send(ctx, "session.started", JSONObject().put("session_id", sessionId).put("device", "phone"))
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
        // The embedded Node side exposes its own RPC server on a separate port (18792),
        // listening for callbacks from Kotlin. We keep this best-effort: drop on failure.
        Thread {
            try {
                Socket(InetAddress.getByName("127.0.0.1"), 18792).use { s ->
                    s.soTimeout = 250
                    val out = OutputStreamWriter(s.getOutputStream())
                    val inn = BufferedReader(InputStreamReader(s.getInputStream()))
                    val req = JSONObject().put("id", "n").put("method", method).put("params", params)
                    out.write(req.toString()); out.write("\n"); out.flush()
                    inn.readLine()
                }
            } catch (e: Exception) {
                Log.v(TAG, "notify $method failed: ${e.message}")
            }
        }.start()
    }
}
