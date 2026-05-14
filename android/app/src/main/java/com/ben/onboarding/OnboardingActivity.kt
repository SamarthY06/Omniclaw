package com.ben.onboarding

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import android.view.View
import android.widget.Button
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.ben.MainActivity
import com.ben.R
import com.ben.pairing.PairingActivity
import com.ben.service.BenForegroundService
import com.ben.util.BenSecrets
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout

/**
 * Three-step linear onboarding:
 *   step 0: grant Accessibility + RECORD_AUDIO + POST_NOTIFICATIONS + CAMERA
 *           runtime perms + battery exemption + (when applicable) OEM-specific
 *           autostart deep-link + offline language pack check.
 *   step 1: paste OpenAI key + set wake phrase.
 *   step 2: scan pairing QR from Mac.
 *
 * On finish: persist secrets, mark onboarding complete, open MainActivity.
 *
 * Why we request RECORD_AUDIO / POST_NOTIFICATIONS / CAMERA *here* instead
 * of at first-use (the way device.* tools do): without RECORD_AUDIO the
 * wake-word recognizer fails silently on first boot - the user just thinks
 * Ben is broken. Without POST_NOTIFICATIONS (Android 13+) the always-on
 * foreground notification never appears, which lets some OEMs aggressively
 * kill us. Camera is needed for QR scanning in step 2 - we ask up front so
 * step 2's scanner doesn't trip an extra dialog.
 *
 * Why an OEM-autostart button: Xiaomi MIUI, Oppo / Realme ColorOS, Vivo
 * FuntouchOS, Huawei EMUI, and OnePlus OxygenOS all kill our foreground
 * service after a few minutes of Doze even when we hold the
 * REQUEST_IGNORE_BATTERY_OPTIMIZATIONS exemption. Each vendor has its own
 * "autostart" / "chain launch" / "background activity" panel buried in
 * settings; we deep-link directly into the right one when we recognize the
 * vendor.
 */
class OnboardingActivity : AppCompatActivity() {
    private val tag = "OnboardingActivity"
    private var step = 0

    private lateinit var stepLabel: android.widget.TextView
    private lateinit var grantAccessibilityBtn: Button
    private lateinit var grantBatteryBtn: Button
    private lateinit var grantRuntimePermsBtn: Button
    private lateinit var oemAutostartBtn: Button
    private lateinit var checkLangBtn: Button
    private lateinit var phraseLayout: TextInputLayout
    private lateinit var phraseInput: TextInputEditText
    private lateinit var openaiLayout: TextInputLayout
    private lateinit var openaiInput: TextInputEditText
    private lateinit var scanQrBtn: Button
    private lateinit var continueBtn: Button

    private val runtimePermsLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { results ->
        results.forEach { (perm, granted) ->
            Log.i(tag, "perm $perm => ${if (granted) "GRANTED" else "DENIED"}")
        }
        renderStep()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)

        stepLabel = findViewById(R.id.onboarding_step_label)
        grantAccessibilityBtn = findViewById(R.id.onboarding_grant_accessibility_btn)
        grantRuntimePermsBtn = findViewById(R.id.onboarding_grant_runtime_btn)
        grantBatteryBtn = findViewById(R.id.onboarding_grant_battery_btn)
        oemAutostartBtn = findViewById(R.id.onboarding_oem_autostart_btn)
        checkLangBtn = findViewById(R.id.onboarding_check_offline_lang_btn)
        phraseLayout = findViewById(R.id.onboarding_phrase_layout)
        phraseInput = findViewById(R.id.onboarding_phrase_input)
        openaiLayout = findViewById(R.id.onboarding_openai_layout)
        openaiInput = findViewById(R.id.onboarding_openai_input)
        scanQrBtn = findViewById(R.id.onboarding_scan_qr_btn)
        continueBtn = findViewById(R.id.onboarding_continue_btn)

        grantAccessibilityBtn.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        grantBatteryBtn.setOnClickListener {
            try {
                startActivity(
                    Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:$packageName"),
                    ),
                )
            } catch (_: ActivityNotFoundException) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }
        grantRuntimePermsBtn.setOnClickListener { requestRuntimePerms() }
        oemAutostartBtn.setOnClickListener { openOemAutostartSettings() }
        checkLangBtn.setOnClickListener { openVoiceInputSettings() }
        scanQrBtn.setOnClickListener {
            startActivity(Intent(this, PairingActivity::class.java))
        }
        continueBtn.setOnClickListener { advanceStep() }

        phraseInput.setText(BenSecrets.wakePhrase(this))
        renderStep()
    }

    override fun onResume() {
        super.onResume()
        renderStep()
    }

    /**
     * Open the page where the user can manage offline speech-recognition
     * language packs. Three problems make this messy:
     *   1. The canonical intent `Settings.ACTION_VOICE_INPUT_SETTINGS` on
     *      Pixel/OnePlus opens the Voice Input services page correctly,
     *      but Samsung One UI 6+ redirects it to "Digital assistant app"
     *      (which is unrelated to speech-recognition language packs).
     *   2. There is no public intent action to deep-link straight into
     *      Google's voice settings; we have to fall back to launching the
     *      Google app and letting the user navigate.
     *   3. Some phones don't have the Google app installed at all (rare
     *      but possible on AOSP / GrapheneOS); we then dump them at the
     *      generic device-settings panel.
     *
     * Strategy: try the canonical voice-input intent first, EXCEPT on
     * Samsung where we skip straight to the Google-app fallback. Then try
     * Google app voice settings. Finally fall back to the generic settings
     * panel with a one-shot Toast telling the user what to look for.
     */
    private fun openVoiceInputSettings() {
        val isSamsung = Build.MANUFACTURER.lowercase().contains("samsung")
        val candidates = mutableListOf<Intent>()
        if (!isSamsung) {
            // Canonical intent first on non-Samsung devices.
            candidates += Intent(Settings.ACTION_VOICE_INPUT_SETTINGS)
        }
        // Google app's main activity. The user has to tap Profile -> Settings
        // -> Voice -> Languages from here, but at least we get them into
        // the right app.
        run {
            val launch = packageManager.getLaunchIntentForPackage("com.google.android.googlequicksearchbox")
            if (launch != null) candidates += launch
        }
        // "Speech Services by Google" app info page - has "Offline speech
        // recognition" inside its settings.
        candidates += Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
            data = Uri.parse("package:com.google.android.tts")
        }
        // Last resort: Samsung's canonical intent (which is wrong, but
        // better than nothing) or the generic settings panel.
        candidates += Intent(Settings.ACTION_VOICE_INPUT_SETTINGS)
        candidates += Intent(Settings.ACTION_SETTINGS)
        var hint: String? = null
        for ((i, intent) in candidates.withIndex()) {
            try {
                startActivity(intent)
                if (i > 0) {
                    // Toast the user-facing hint - they need to know what
                    // to look for now that we couldn't deep-link.
                    hint = when (intent.action) {
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS -> "Open this app -> Offline speech recognition -> download English."
                        Settings.ACTION_SETTINGS -> "Search Settings for 'Voice input' or 'Speech Services' and install English pack."
                        else -> "In Google: Profile -> Settings -> Voice -> Languages -> add English."
                    }
                }
                if (hint != null) {
                    Toast.makeText(this, hint, Toast.LENGTH_LONG).show()
                }
                return
            } catch (_: ActivityNotFoundException) {
                Log.w("OnboardingActivity", "voice-input candidate failed: ${intent.action ?: intent.component}")
                continue
            } catch (e: Exception) {
                Log.w("OnboardingActivity", "voice-input candidate threw: ${e.message}")
                continue
            }
        }
        Toast.makeText(this, "Could not open language settings. Open Settings and search 'Voice input'.", Toast.LENGTH_LONG).show()
    }

    private fun requestRuntimePerms() {
        val needed = mutableListOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        val toRequest = needed.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }.toTypedArray()
        if (toRequest.isEmpty()) {
            Toast.makeText(this, R.string.onboarding_runtime_perms_already_granted, Toast.LENGTH_SHORT).show()
            return
        }
        runtimePermsLauncher.launch(toRequest)
    }

    private fun openOemAutostartSettings() {
        val mfr = Build.MANUFACTURER.lowercase()
        val brand = Build.BRAND.lowercase()
        val tried = mutableListOf<Intent>()
        when {
            mfr.contains("xiaomi") || brand.contains("redmi") || brand.contains("poco") -> {
                tried += Intent().setComponent(
                    ComponentName(
                        "com.miui.securitycenter",
                        "com.miui.permcenter.autostart.AutoStartManagementActivity",
                    ),
                )
            }
            mfr.contains("oppo") || brand.contains("realme") -> {
                tried += Intent().setComponent(
                    ComponentName(
                        "com.coloros.safecenter",
                        "com.coloros.safecenter.permission.startup.StartupAppListActivity",
                    ),
                )
                tried += Intent().setComponent(
                    ComponentName(
                        "com.coloros.safecenter",
                        "com.coloros.safecenter.startupapp.StartupAppListActivity",
                    ),
                )
            }
            mfr.contains("vivo") -> {
                tried += Intent().setComponent(
                    ComponentName(
                        "com.iqoo.secure",
                        "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity",
                    ),
                )
                tried += Intent().setComponent(
                    ComponentName(
                        "com.vivo.permissionmanager",
                        "com.vivo.permissionmanager.activity.BgStartUpManagerActivity",
                    ),
                )
            }
            mfr.contains("huawei") || mfr.contains("honor") -> {
                tried += Intent().setComponent(
                    ComponentName(
                        "com.huawei.systemmanager",
                        "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity",
                    ),
                )
                tried += Intent().setComponent(
                    ComponentName(
                        "com.huawei.systemmanager",
                        "com.huawei.systemmanager.optimize.process.ProtectActivity",
                    ),
                )
            }
            mfr.contains("samsung") -> {
                // Samsung's "Sleeping apps" panel has no deep link - take
                // the user to app-info and let them dig from there.
                tried += Intent(
                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:$packageName"),
                )
            }
            mfr.contains("oneplus") -> {
                tried += Intent().setComponent(
                    ComponentName(
                        "com.oneplus.security",
                        "com.oneplus.security.chainlaunch.view.ChainLaunchAppListActivity",
                    ),
                )
            }
            else -> {
                tried += Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            }
        }
        for (intent in tried) {
            try {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                startActivity(intent)
                return
            } catch (_: Exception) { /* try next */ }
        }
        Toast.makeText(this, R.string.onboarding_oem_autostart_unknown, Toast.LENGTH_LONG).show()
    }

    private fun renderStep() {
        when (step) {
            0 -> {
                stepLabel.text = getString(R.string.onboarding_step_perms)
                grantAccessibilityBtn.visibility = View.VISIBLE
                grantRuntimePermsBtn.visibility = View.VISIBLE
                grantBatteryBtn.visibility = View.VISIBLE
                oemAutostartBtn.visibility = if (isKnownAggressiveOem()) View.VISIBLE else View.GONE
                checkLangBtn.visibility = View.VISIBLE
                phraseLayout.visibility = View.GONE
                openaiLayout.visibility = View.GONE
                scanQrBtn.visibility = View.GONE
                continueBtn.text = getString(R.string.onboarding_continue)
                grantRuntimePermsBtn.text = if (allRuntimePermsGranted()) {
                    getString(R.string.onboarding_runtime_perms_granted)
                } else {
                    getString(R.string.onboarding_grant_runtime)
                }
            }
            1 -> {
                stepLabel.text = getString(R.string.onboarding_step_keys)
                grantAccessibilityBtn.visibility = View.GONE
                grantRuntimePermsBtn.visibility = View.GONE
                grantBatteryBtn.visibility = View.GONE
                oemAutostartBtn.visibility = View.GONE
                checkLangBtn.visibility = View.GONE
                phraseLayout.visibility = View.VISIBLE
                openaiLayout.visibility = View.VISIBLE
                scanQrBtn.visibility = View.GONE
                continueBtn.text = getString(R.string.onboarding_continue)
            }
            2 -> {
                stepLabel.text = getString(R.string.onboarding_step_pair)
                grantAccessibilityBtn.visibility = View.GONE
                grantRuntimePermsBtn.visibility = View.GONE
                grantBatteryBtn.visibility = View.GONE
                oemAutostartBtn.visibility = View.GONE
                checkLangBtn.visibility = View.GONE
                phraseLayout.visibility = View.GONE
                openaiLayout.visibility = View.GONE
                scanQrBtn.visibility = View.VISIBLE
                continueBtn.text = getString(R.string.onboarding_finish)
            }
        }
    }

    private fun isKnownAggressiveOem(): Boolean {
        val s = (Build.MANUFACTURER + " " + Build.BRAND).lowercase()
        return listOf(
            "xiaomi", "redmi", "poco", "oppo", "realme",
            "vivo", "huawei", "honor", "oneplus",
        ).any { s.contains(it) }
    }

    private fun allRuntimePermsGranted(): Boolean {
        val needed = mutableListOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        return needed.all {
            ContextCompat.checkSelfPermission(this, it) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun advanceStep() {
        when (step) {
            0 -> {
                if (!allRuntimePermsGranted()) {
                    Toast.makeText(this, R.string.onboarding_runtime_perms_required, Toast.LENGTH_LONG).show()
                    return
                }
                step = 1; renderStep()
            }
            1 -> {
                val phrase = phraseInput.text?.toString()?.trim().orEmpty()
                val key = openaiInput.text?.toString()?.trim().orEmpty()
                if (key.isBlank()) {
                    Toast.makeText(this, "OpenAI API key is required", Toast.LENGTH_SHORT).show()
                    return
                }
                BenSecrets.setOpenaiKey(this, key)
                if (phrase.isNotBlank()) BenSecrets.setWakePhrase(this, phrase)
                step = 2
                renderStep()
            }
            2 -> {
                BenSecrets.setOnboardingComplete(this, true)
                BenForegroundService.startIfNeeded(this)
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }
        }
    }
}
