package com.ben.pairing

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.ben.R
import com.ben.util.BenSecrets
import com.journeyapps.barcodescanner.BarcodeCallback
import com.journeyapps.barcodescanner.BarcodeResult
import com.journeyapps.barcodescanner.DecoratedBarcodeView
import org.json.JSONObject

/**
 * Pairing flow:
 *   1. Camera scanner reads a `ben://pair?host=...&port=...&fp=...&secret=...&role=mac&id=...&v=1`
 *      URL (legacy `jarvis://pair?...` URLs are still accepted for backward
 *      compatibility with older Mac builds).
 *   2. We parse it (Kotlin-side) and persist into BenSecrets.
 *   3. We hand off to the embedded Node bridge (via NodeBridgeService JSON-RPC)
 *      which connects to the Mac peer daemon and completes mutual auth.
 *   4. We then poll `peer.pair_status` on the Node bridge for up to 5 s. ONLY
 *      if it returns `paired: true` do we show the success toast. Otherwise
 *      we show an explicit "handshake didn't confirm" warning so the user
 *      doesn't walk away thinking pairing worked when the Mac peer is
 *      unreachable / offline / on a different network.
 *
 * Three entry paths land here, all converge on handleScanned():
 *   a) QR camera scan                   - the default UX.
 *   b) "Paste pairing URI" button       - reads ClipboardManager. Useful when
 *                                         the Mac terminal QR is too small or
 *                                         the camera struggles in low light.
 *   c) ben:// or jarvis:// deep link    - tapping a WhatsApp / Mail link with
 *                                         the URI opens the PairingDeepLink
 *                                         activity-alias which routes here
 *                                         with intent.data set.
 */
class PairingActivity : AppCompatActivity() {
    private val tag = "PairingActivity"
    private lateinit var scanner: DecoratedBarcodeView
    private lateinit var status: TextView
    private lateinit var pasteBtn: Button
    private val mainHandler = Handler(Looper.getMainLooper())

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startScanner()
        } else {
            scanner.visibility = View.GONE
            status.text = getString(R.string.pairing_camera_blocked)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_pairing)
        scanner = findViewById(R.id.pair_scanner)
        status = findViewById(R.id.pair_status)
        pasteBtn = findViewById(R.id.pair_paste_btn)

        pasteBtn.setOnClickListener { handlePasteIntent() }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED
        ) {
            startScanner()
        } else {
            cameraPermissionLauncher.launch(Manifest.permission.CAMERA)
        }

        // Allow incoming ben://pair (or legacy jarvis://pair) URIs from
        // outside (deep link / share).
        intent?.data?.let { handleScanned(it.toString()) }
    }

    override fun onResume() {
        super.onResume()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA)
            == PackageManager.PERMISSION_GRANTED && scanner.visibility == View.VISIBLE
        ) {
            scanner.resume()
        }
    }

    override fun onPause() {
        super.onPause()
        try { scanner.pause() } catch (_: Exception) {}
    }

    private fun startScanner() {
        scanner.visibility = View.VISIBLE
        scanner.decodeContinuous(object : BarcodeCallback {
            override fun barcodeResult(result: BarcodeResult?) {
                val raw = result?.text ?: return
                handleScanned(raw)
            }
        })
        scanner.resume()
    }

    private fun handlePasteIntent() {
        val cb = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val clip = cb?.primaryClip
        if (clip == null || clip.itemCount == 0) {
            Toast.makeText(this, R.string.pairing_paste_empty, Toast.LENGTH_SHORT).show()
            return
        }
        val text = clip.getItemAt(0).coerceToText(this)?.toString().orEmpty().trim()
        if (text.isEmpty()) {
            Toast.makeText(this, R.string.pairing_paste_empty, Toast.LENGTH_SHORT).show()
            return
        }
        if (!isPairingUri(text)) {
            Toast.makeText(this, R.string.pairing_paste_invalid, Toast.LENGTH_SHORT).show()
            return
        }
        handleScanned(text)
    }

    private fun handleScanned(rawUrl: String) {
        val parsed = parsePairingUri(rawUrl) ?: run {
            status.text = getString(R.string.pairing_failed, "invalid URL")
            return
        }
        try { scanner.pause() } catch (_: Exception) {}
        BenSecrets.setPeer(
            ctx = this,
            deviceId = parsed.optString("id"),
            host = parsed.optString("host"),
            port = parsed.optInt("port", 18790),
            secretB64 = parsed.optString("secret"),
        )
        status.text = "Connecting…"
        // Kick the embedded Node bridge to (re)connect with the new secret,
        // then poll peer.pair_status until it confirms or times out.
        Thread {
            val pairOk = try {
                bridgeCall("peer.pair_now", JSONObject())
                // Give the Node side a moment to actually finish the WSS
                // handshake before we ask whether it succeeded.
                Thread.sleep(1500)
                pollPairStatus(maxWaitMs = 5_000L)
            } catch (e: Exception) {
                Log.w(tag, "peer.pair_now/status failed", e)
                false
            }
            mainHandler.post { onPairResult(pairOk) }
        }.start()
    }

    private fun onPairResult(verified: Boolean) {
        if (verified) {
            Toast.makeText(this, R.string.pairing_success, Toast.LENGTH_SHORT).show()
            status.text = getString(R.string.pairing_success)
            finish()
        } else {
            // We persisted the secret already, so "Re-pair with Mac" in
            // settings can retry without rescanning. But we are NOT closing
            // the activity here - the user should know it didn't fully work.
            Toast.makeText(this, R.string.pairing_status_unverified, Toast.LENGTH_LONG).show()
            status.text = getString(R.string.pairing_status_unverified)
            // Re-enable scanner so they can try a fresh QR if the Mac is
            // restarted.
            try {
                scanner.visibility = View.VISIBLE
                scanner.resume()
            } catch (_: Exception) {}
        }
    }

    /**
     * Poll `peer.pair_status` on the embedded Node bridge until it returns
     * `paired: true` or [maxWaitMs] elapses. Returns true only if confirmed.
     */
    private fun pollPairStatus(maxWaitMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + maxWaitMs
        var lastErr: String? = null
        while (System.currentTimeMillis() < deadline) {
            val resp = try {
                bridgeCall("peer.pair_status", JSONObject())
            } catch (e: Exception) {
                lastErr = e.message
                null
            }
            val result = resp?.optJSONObject("result")
            if (result != null && result.optBoolean("paired", false)) {
                Log.i(tag, "peer.pair_status confirmed")
                return true
            }
            if (result != null) lastErr = result.optString("error", lastErr ?: "")
            try { Thread.sleep(500) } catch (_: InterruptedException) { return false }
        }
        Log.w(tag, "peer.pair_status timed out: $lastErr")
        return false
    }

    /**
     * One-shot newline-JSON-RPC call to the NodeBridgeService inbound port.
     * Returns the full response envelope, or null on transport failure. The
     * inbound port (18792) is the same one BenVoiceService uses for tools.list
     * and session.* lifecycle pings.
     */
    private fun bridgeCall(method: String, params: JSONObject): JSONObject? {
        val req = JSONObject()
            .put("id", "pair-${System.nanoTime()}")
            .put("method", method)
            .put("params", params)
        java.net.Socket().use { sock ->
            sock.connect(
                java.net.InetSocketAddress(java.net.InetAddress.getLoopbackAddress(), 18792),
                1500,
            )
            sock.soTimeout = 5_000
            val out = java.io.OutputStreamWriter(sock.getOutputStream(), Charsets.UTF_8)
            val inn = java.io.BufferedReader(java.io.InputStreamReader(sock.getInputStream(), Charsets.UTF_8))
            out.write(req.toString()); out.write("\n"); out.flush()
            val line = inn.readLine() ?: return null
            return JSONObject(line)
        }
    }

    /**
     * Pairing URI accepts both schemes:
     *   ben://pair?...       (canonical, post-rebrand)
     *   jarvis://pair?...    (legacy, kept so older Mac QRs still work)
     */
    private fun isPairingUri(raw: String): Boolean =
        raw.startsWith("ben://pair") || raw.startsWith("jarvis://pair")

    private fun parsePairingUri(raw: String): JSONObject? {
        if (!isPairingUri(raw)) return null
        val q = raw.substringAfter('?', "")
        val out = JSONObject()
        for (kv in q.split('&')) {
            if (kv.isBlank()) continue
            val (k, v) = kv.split('=', limit = 2).let { it[0] to (it.getOrNull(1) ?: "") }
            val decoded = java.net.URLDecoder.decode(v, "UTF-8")
            if (k == "port") out.put(k, decoded.toIntOrNull() ?: 18790) else out.put(k, decoded)
        }
        if (!out.has("host") || !out.has("secret")) return null
        return out
    }
}
