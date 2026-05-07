package com.ben.bridge

import android.content.Context
import com.ben.service.NodeBridgeService
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.Socket
import org.json.JSONObject

/**
 * Tiny client-side helper to talk to the NodeBridgeService JSON-RPC server.
 * Mostly used by Home tab to display "node bridge: ok / starting" status.
 *
 * NOTE: this is the *server* side from the Node payload's POV. Production calls
 * happen the other way - Node opens the socket and sends RPCs to us. This is
 * just a self-loop probe (we connect and call ping locally).
 */
object NodeRpcProbe {
    fun ping(@Suppress("UNUSED_PARAMETER") ctx: Context): Boolean {
        return try {
            Socket(InetAddress.getByName("127.0.0.1"), NodeBridgeService.JSON_RPC_PORT).use { s ->
                s.soTimeout = 250
                val out = OutputStreamWriter(s.getOutputStream())
                val inn = BufferedReader(InputStreamReader(s.getInputStream()))
                val req = JSONObject()
                    .put("id", "probe")
                    .put("method", "ping")
                    .put("params", JSONObject().put("echo", "home"))
                out.write(req.toString())
                out.write("\n")
                out.flush()
                val reply = inn.readLine() ?: return false
                val parsed = JSONObject(reply)
                parsed.has("result") && parsed.getJSONObject("result").optBoolean("pong", false)
            }
        } catch (_: Exception) {
            false
        }
    }
}
