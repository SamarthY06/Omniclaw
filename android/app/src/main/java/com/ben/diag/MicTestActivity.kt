package com.ben.diag

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.text.method.ScrollingMovementMethod
import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityManager
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.ben.R
import com.ben.service.BenVoiceService
import com.ben.service.BenWakewordService
import com.ben.util.BenSecrets
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Diagnostic screen the user can open from the Home tab when the wake word is
 * "not working". Shows:
 *   * permission / battery / accessibility status (with a one-tap "Open
 *     settings" jump for whichever is missing),
 *   * the live event stream from BenWakewordService (READY / BEGIN / PARTIAL
 *     / RESULT / WAKE_MATCH / ERROR / RESTART),
 *   * a "Force restart wake listener" button that asks the service to
 *     destroy + recreate its SpeechRecognizer (reset offline-pack fallback
 *     state, etc.).
 *
 * If the user says "Ben" and nothing happens, this screen will tell us why:
 *   * No PARTIAL events at all -> mic permission denied OR offline pack
 *     missing AND we never fell back to network. Tap "Force restart".
 *   * PARTIAL events but no WAKE_MATCH -> the recognizer is hearing the
 *     user but the matcher is rejecting. They can SEE the rejected
 *     transcripts and tell us.
 *   * ERROR_INSUFFICIENT_PERMISSIONS -> RECORD_AUDIO not granted; the
 *     status row above flags it red.
 *   * ERROR_LANGUAGE_NOT_SUPPORTED -> install the en-US offline pack.
 */
class MicTestActivity : AppCompatActivity() {

    private lateinit var phraseView: TextView
    private lateinit var stateView: TextView
    private lateinit var permAudio: TextView
    private lateinit var permBattery: TextView
    private lateinit var permAccessibility: TextView
    private lateinit var logView: TextView
    private lateinit var forceRestart: Button
    private lateinit var openSettings: Button
    private lateinit var clearLog: Button

    private val sdf = SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
    private val log = StringBuilder()

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val ts = intent.getLongExtra(BenWakewordService.EXTRA_TS, System.currentTimeMillis())
            val kind = intent.getStringExtra(BenWakewordService.EXTRA_KIND) ?: "?"
            val detail = intent.getStringExtra(BenWakewordService.EXTRA_DETAIL) ?: ""
            appendLog(ts, "WAKE/$kind", detail)
            if (kind == "WAKE_MATCH" || kind == "RESTART" || kind == "ERROR") {
                refreshState()
            }
        }
    }

    private val voiceReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val ts = intent.getLongExtra(BenVoiceService.EXTRA_TS, System.currentTimeMillis())
            val kind = intent.getStringExtra(BenVoiceService.EXTRA_KIND) ?: "?"
            val detail = intent.getStringExtra(BenVoiceService.EXTRA_DETAIL) ?: ""
            appendLog(ts, "VOICE/$kind", detail)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_mic_test)

        phraseView = findViewById(R.id.mic_test_phrase)
        stateView = findViewById(R.id.mic_test_state)
        permAudio = findViewById(R.id.mic_test_perm_audio)
        permBattery = findViewById(R.id.mic_test_perm_battery)
        permAccessibility = findViewById(R.id.mic_test_perm_accessibility)
        logView = findViewById(R.id.mic_test_log)
        forceRestart = findViewById(R.id.mic_test_force_restart)
        openSettings = findViewById(R.id.mic_test_open_settings)
        clearLog = findViewById(R.id.mic_test_clear_log)

        logView.movementMethod = ScrollingMovementMethod()

        forceRestart.setOnClickListener { BenWakewordService.forceRestart(this) }
        openSettings.setOnClickListener { openAppSettings() }
        clearLog.setOnClickListener {
            log.setLength(0)
            BenWakewordService.eventBuffer.clear()
            BenVoiceService.voiceEventBuffer.clear()
            logView.text = "(log cleared - say your wake phrase now)"
        }

        // Seed log with whatever events the wake AND voice services already
        // buffered before the user opened this screen. Merge by timestamp
        // so the user sees a single chronological stream.
        val seeded = mutableListOf<Triple<Long, String, String>>()
        for (ev in BenWakewordService.eventBuffer) {
            seeded += Triple(ev.ts, "WAKE/${ev.kind.name}", ev.detail)
        }
        for (ev in BenVoiceService.voiceEventBuffer) {
            seeded += Triple(ev.ts, "VOICE/${ev.kind}", ev.detail)
        }
        seeded.sortedBy { it.first }.forEach { (ts, kind, detail) -> appendLog(ts, kind, detail) }
        if (log.isEmpty()) {
            logView.text = "(no events yet — say your wake phrase now)"
        }
    }

    override fun onResume() {
        super.onResume()
        LocalBroadcastManager.getInstance(this)
            .registerReceiver(receiver, IntentFilter(BenWakewordService.ACTION_EVENT))
        LocalBroadcastManager.getInstance(this)
            .registerReceiver(voiceReceiver, IntentFilter(BenVoiceService.ACTION_VOICE_EVENT))
        refreshState()
    }

    override fun onPause() {
        super.onPause()
        LocalBroadcastManager.getInstance(this).unregisterReceiver(receiver)
        LocalBroadcastManager.getInstance(this).unregisterReceiver(voiceReceiver)
    }

    private fun appendLog(ts: Long, kind: String, detail: String) {
        val line = "${sdf.format(Date(ts))}  [$kind] $detail\n"
        log.append(line)
        // Keep last ~10 KB so the view stays snappy
        if (log.length > 10_000) log.delete(0, log.length - 10_000)
        logView.text = log
    }

    private fun refreshState() {
        phraseView.text = "${getString(R.string.mic_test_phrase)}: ${BenSecrets.wakePhrase(this)}"

        // RECORD_AUDIO
        val audioOk = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        permAudio.text = "${getString(R.string.mic_test_perm_record_audio)}: ${statusLabel(audioOk)}"

        // Battery exemption
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        val battOk = if (Build.VERSION.SDK_INT >= 23) pm.isIgnoringBatteryOptimizations(packageName) else true
        permBattery.text = "${getString(R.string.mic_test_perm_battery)}: ${statusLabel(battOk)}"

        // Accessibility service
        val accOk = isAccessibilityServiceEnabled()
        permAccessibility.text = "${getString(R.string.mic_test_perm_accessibility)}: ${statusLabel(accOk)}"

        // Service running state - infer from the eventBuffer's most recent
        // RESTART event. paused is also set by ACTION_PAUSE.
        val recent = BenWakewordService.eventBuffer.toList().lastOrNull { it.kind == BenWakewordService.EventKind.RESTART }
        val state = when {
            recent == null -> getString(R.string.mic_test_state_stopped)
            recent.detail.contains("paused", ignoreCase = true) -> getString(R.string.mic_test_state_paused)
            else -> getString(R.string.mic_test_state_running)
        }
        stateView.text = state
    }

    private fun statusLabel(ok: Boolean): String =
        if (ok) "✓ " + getString(R.string.mic_test_status_ok)
        else "✗ " + getString(R.string.mic_test_status_missing)

    private fun isAccessibilityServiceEnabled(): Boolean {
        val am = getSystemService(ACCESSIBILITY_SERVICE) as? AccessibilityManager ?: return false
        val enabled = am.getEnabledAccessibilityServiceList(AccessibilityServiceInfo.FEEDBACK_ALL_MASK)
        return enabled.any { it.id?.contains(packageName) == true }
    }

    private fun openAppSettings() {
        startActivity(
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.fromParts("package", packageName, null)
                flags = Intent.FLAG_ACTIVITY_NEW_TASK
            }
        )
    }
}
