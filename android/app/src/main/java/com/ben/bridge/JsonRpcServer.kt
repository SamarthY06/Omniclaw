package com.ben.bridge

import android.util.Log
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.charset.StandardCharsets
import kotlin.concurrent.thread
import org.json.JSONObject

typealias JsonRpcHandler = (JSONObject) -> JSONObject

/**
 * Minimal newline-delimited JSON-RPC 2.0 server, bound to 127.0.0.1 only.
 *
 * Wire format (one JSON object per line, framed by '\n'):
 *   request:  {"id":"<uuid>","method":"<name>","params":{...}}
 *   response: {"id":"<uuid>","result":{...}}              -- success
 *             {"id":"<uuid>","error":{"message":"...","code":-1}} -- failure
 *
 * Used by NodeBridgeService to expose Kotlin-side capabilities (Accessibility,
 * MediaProjection, ML Kit OCR, secrets) to the embedded Node runtime.
 */
class JsonRpcServer(
    private val port: Int,
    private val handlers: Map<String, JsonRpcHandler>,
) {
    private val tag = "JsonRpcServer"
    @Volatile private var running = false
    private var serverSocket: ServerSocket? = null

    fun start() {
        if (running) return
        running = true
        thread(name = "JsonRpcServer-acceptor", isDaemon = true) {
            try {
                val s = ServerSocket(port, 32, InetAddress.getByName("127.0.0.1"))
                serverSocket = s
                Log.i(tag, "JSON-RPC server listening on 127.0.0.1:$port")
                while (running) {
                    val client = try {
                        s.accept()
                    } catch (e: IOException) {
                        if (!running) return@thread
                        Log.w(tag, "accept failed", e)
                        continue
                    }
                    thread(name = "JsonRpcServer-client", isDaemon = true) { handleClient(client) }
                }
            } catch (e: Exception) {
                Log.e(tag, "Server crashed", e)
            }
        }
    }

    fun stop() {
        running = false
        try {
            serverSocket?.close()
        } catch (_: IOException) {
        }
        serverSocket = null
    }

    private fun handleClient(socket: Socket) {
        try {
            socket.tcpNoDelay = true
            BufferedReader(InputStreamReader(socket.getInputStream(), StandardCharsets.UTF_8)).use { reader ->
                OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8).use { writer ->
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isBlank()) continue
                        val response = dispatch(line)
                        writer.write(response.toString())
                        writer.write("\n")
                        writer.flush()
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(tag, "client error", e)
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    private fun dispatch(line: String): JSONObject {
        val req = try {
            JSONObject(line)
        } catch (e: Exception) {
            return errorResponse(null, "parse_error: ${e.message}")
        }
        val id = req.optString("id", null)
        val method = req.optString("method", "")
        val params = req.optJSONObject("params") ?: JSONObject()
        val handler = handlers[method] ?: return errorResponse(id, "unknown_method:$method")
        return try {
            JSONObject().apply {
                if (id != null) put("id", id)
                put("result", handler(params))
            }
        } catch (e: Exception) {
            Log.w(tag, "handler '$method' threw", e)
            errorResponse(id, "${e.javaClass.simpleName}: ${e.message ?: ""}")
        }
    }

    private fun errorResponse(id: String?, message: String): JSONObject {
        return JSONObject().apply {
            if (id != null) put("id", id)
            put("error", JSONObject().put("message", message).put("code", -1))
        }
    }
}
