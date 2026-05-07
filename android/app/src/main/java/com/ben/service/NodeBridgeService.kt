package com.ben.service

import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import com.ben.bridge.AndroidAxBridge
import com.ben.bridge.AndroidDeviceBridge
import com.ben.bridge.AndroidOcrBridge
import com.ben.bridge.AndroidScreencapBridge
import com.ben.bridge.JsonRpcServer
import com.ben.util.WorkspaceBootstrap
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Boots a single instance of the nodejs-mobile runtime, keeps it alive for the
 * lifetime of the service, and exposes [JsonRpcServer] (127.0.0.1:18791) so
 * embedded TS can call back into Kotlin for AccessibilityService / MediaProjection
 * / ML Kit OCR.
 *
 * The reflection trick around `org.nodejs.android.NodeJS` keeps the build green
 * even when the AAR hasn't been dropped into app/libs yet (e.g. when the project
 * is opened in Android Studio for the first time before `fetch-nodejs-mobile.sh`
 * runs). At runtime, the AAR is required.
 */
class NodeBridgeService : Service() {
    private val tag = "NodeBridgeService"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var nodeJob: Job? = null
    private var rpcServer: JsonRpcServer? = null

    override fun onCreate() {
        super.onCreate()
        Log.i(tag, "Starting embedded Node + JSON-RPC bridge")
        // 1. Make sure assets/node has been copied to filesDir/openclaw and workspace is seeded.
        val nodeRoot: File = WorkspaceBootstrap.ensureNodeRoot(this)
        val workspaceRoot: File = WorkspaceBootstrap.ensureWorkspace(this)

        // 2. Spin up the JSON-RPC bridge on 127.0.0.1:18791 BEFORE Node starts so the
        // embedded JS can connect immediately.
        rpcServer = JsonRpcServer(
            port = JSON_RPC_PORT,
            handlers = mapOf(
                "ax.tree" to { args -> AndroidAxBridge.tree(this, args) },
                "ax.click" to { args -> AndroidAxBridge.clickByAxId(this, args) },
                "ax.click_at" to { args -> AndroidAxBridge.clickAt(this, args) },
                "ax.type" to { args -> AndroidAxBridge.type(this, args) },
                "ax.swipe" to { args -> AndroidAxBridge.swipe(this, args) },
                "ax.scroll" to { args -> AndroidAxBridge.scroll(this, args) },
                "ax.focus" to { args -> AndroidAxBridge.focus(this, args) },
                "ax.launch_app" to { args -> AndroidAxBridge.launchApp(this, args) },
                "ax.screen_size" to { args -> AndroidAxBridge.screenSize(this, args) },
                "ax.screenshot" to { args -> AndroidScreencapBridge.screenshot(this, args) },
                "ocr.recognize_text" to { args -> AndroidOcrBridge.recognize(this, args) },
                "secrets.peer" to { args ->
                    JSONObject().apply {
                        val ctx = this@NodeBridgeService
                        put("device_id", com.ben.util.BenSecrets.peerDeviceId(ctx))
                        put("host", com.ben.util.BenSecrets.peerHost(ctx))
                        put("port", com.ben.util.BenSecrets.peerPort(ctx))
                        put("secret_b64", com.ben.util.BenSecrets.peerSecretB64(ctx))
                        put("own_device_id", com.ben.util.BenSecrets.ownDeviceId(ctx))
                    }
                },
                "secrets.set_peer" to { args ->
                    val ctx = this@NodeBridgeService
                    com.ben.util.BenSecrets.setPeer(
                        ctx,
                        deviceId = args.optString("device_id"),
                        host = args.optString("host"),
                        port = args.optInt("port", 18790),
                        secretB64 = args.optString("secret_b64"),
                    )
                    JSONObject().put("ok", true)
                },
                "secrets.openai" to { _ ->
                    JSONObject().put(
                        "key",
                        com.ben.util.BenSecrets.openaiKey(this@NodeBridgeService) ?: "",
                    )
                },
                "ping" to { args ->
                    JSONObject().put("pong", true).put("echo", args.optString("echo"))
                },
                // device.* handlers - native Android API surfaces exposed to
                // the Realtime model via OpenClaw tool registry. See
                // assets/node/src/openclaw/device_tools.js for the JSON
                // schema; the Kotlin side here is the actual implementation.
                "device.get_location" to { args -> AndroidDeviceBridge.getLocation(this, args) },
                "device.get_contacts" to { args -> AndroidDeviceBridge.getContacts(this, args) },
                "device.place_call" to { args -> AndroidDeviceBridge.placeCall(this, args) },
                "device.launch_app" to { args -> AndroidDeviceBridge.launchApp(this, args) },
                "device.clipboard_get" to { args -> AndroidDeviceBridge.clipboardGet(this, args) },
                "device.clipboard_set" to { args -> AndroidDeviceBridge.clipboardSet(this, args) },
                "device.battery_status" to { args -> AndroidDeviceBridge.batteryStatus(this, args) },
            ),
        ).also { it.start() }

        // 3. Boot Node with our entrypoint. nodejs-mobile is loaded reflectively so
        // a missing AAR is a runtime error rather than a build error.
        nodeJob = scope.launch {
            startNodeRuntime(nodeRoot, workspaceRoot)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY
    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        rpcServer?.stop()
        scope.cancel()
        nodeJob = null
    }

    private fun startNodeRuntime(nodeRoot: File, workspaceRoot: File) {
        val entrypoint = File(nodeRoot, "index.js").absolutePath
        // nodejs-mobile takes its environment via real envvars before node::Start();
        // we set them on the JVM process so any libuv-spawned children inherit them.
        System.setProperty("BEN_NODE_ROOT", nodeRoot.absolutePath)
        System.setProperty("BEN_WORKSPACE", workspaceRoot.absolutePath)
        System.setProperty("BEN_RPC_PORT", JSON_RPC_PORT.toString())
        System.setProperty("BEN_DEVICE_ROLE", "android")
        try {
            android.system.Os.setenv("BEN_NODE_ROOT", nodeRoot.absolutePath, true)
            android.system.Os.setenv("BEN_WORKSPACE", workspaceRoot.absolutePath, true)
            android.system.Os.setenv("BEN_RPC_PORT", JSON_RPC_PORT.toString(), true)
            android.system.Os.setenv("BEN_DEVICE_ROLE", "android", true)
        } catch (_: Throwable) {
            // Os.setenv unavailable on some forks; the System.setProperty path covers JVM lookups.
        }
        try {
            Log.i(tag, "Starting nodejs-mobile with entry $entrypoint")
            val rc = com.ben.NodeJS.startNode(arrayOf("node", entrypoint))
            Log.i(tag, "nodejs-mobile exited rc=$rc")
        } catch (e: UnsatisfiedLinkError) {
            Log.e(
                tag,
                "libnode.so / libbennode.so missing - run scripts/fetch-nodejs-mobile.sh and rebuild.",
                e,
            )
        } catch (e: Throwable) {
            Log.e(tag, "Failed to start embedded Node runtime", e)
        }
    }

    companion object {
        const val JSON_RPC_PORT = 18791
    }
}
