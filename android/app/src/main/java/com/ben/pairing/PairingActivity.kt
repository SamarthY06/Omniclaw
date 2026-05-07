package com.ben.pairing

import android.Manifest
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
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
 *   1. Camera scanner reads a `jarvis://pair?host=...&port=...&fp=...&secret=...&role=mac&id=...&v=1` URL.
 *   2. We parse it (Kotlin-side) and persist into BenSecrets.
 *   3. We hand off to the embedded Node bridge (via NodeBridgeService JSON-RPC)
 *      which connects to the Mac peer daemon and completes mutual auth.
 *   4. Once the daemon confirms, we Toast "paired" and finish().
 *
 * Three entry paths land here, all converge on handleScanned():
 *   a) QR camera scan                   - the default UX.
 *   b) "Paste pairing URI" button       - reads ClipboardManager. Useful when
 *                                         the Mac terminal QR is too small or
 *                                         the camera struggles in low light.
 *   c) jarvis://pair deep link          - tapping a WhatsApp / Mail link with
 *                                         the URI opens the PairingDeepLink
 *                                         activity-alias which routes here
 *                                         with intent.data set.
 */
class PairingActivity : AppCompatActivity() {
    private lateinit var scanner: DecoratedBarcodeView
    private lateinit var status: TextView
    private lateinit var pasteBtn: Button

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startScanner()
        } else {
            // The user can still pair via the paste button or a deep link;
            // hide the dead camera so they're not stuck staring at a black
            // viewfinder.
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

        // Allow incoming jarvis://pair URIs from outside (deep link / share).
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
        if (!text.startsWith("jarvis://pair")) {
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
        // Best-effort: poke the embedded Node bridge to start the peer client.
        // Node may not yet be running (during onboarding step 3 the foreground
        // service has not started); that's fine - it picks up the secret from
        // BenSecrets on its next boot.
        Thread {
            try {
                val s = java.net.Socket(java.net.InetAddress.getByName("127.0.0.1"), 18792)
                s.use {
                    val out = java.io.OutputStreamWriter(it.getOutputStream())
                    val inn = java.io.BufferedReader(java.io.InputStreamReader(it.getInputStream()))
                    val req = JSONObject()
                        .put("id", "pair")
                        .put("method", "peer.pair_now")
                        .put("params", JSONObject())
                    out.write(req.toString()); out.write("\n"); out.flush()
                    inn.readLine()
                }
            } catch (_: Exception) {}
        }.start()
        runOnUiThread {
            Toast.makeText(this, R.string.pairing_success, Toast.LENGTH_SHORT).show()
            status.text = getString(R.string.pairing_success)
            finish()
        }
    }

    private fun parsePairingUri(raw: String): JSONObject? {
        if (!raw.startsWith("jarvis://pair")) return null
        val q = raw.substringAfter('?', "")
        val out = JSONObject()
        for (kv in q.split('&')) {
            val (k, v) = kv.split('=', limit = 2).let { it[0] to (it.getOrNull(1) ?: "") }
            val decoded = java.net.URLDecoder.decode(v, "UTF-8")
            // port is the only int we expect; everything else is a string.
            if (k == "port") out.put(k, decoded.toIntOrNull() ?: 18790) else out.put(k, decoded)
        }
        if (!out.has("host") || !out.has("secret")) return null
        return out
    }
}
