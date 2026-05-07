package com.ben.onboarding

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.ben.MainActivity
import com.ben.R
import com.ben.pairing.PairingActivity
import com.ben.service.BenForegroundService
import com.ben.util.BenSecrets
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.textfield.TextInputLayout

/**
 * Three-step linear onboarding:
 *   step 0: grant Accessibility + battery exemption + offline language pack check.
 *   step 1: paste OpenAI key + set wake phrase.
 *   step 2: scan pairing QR from Mac.
 *
 * On finish: persist secrets, mark onboarding complete, open MainActivity.
 */
class OnboardingActivity : AppCompatActivity() {
    private var step = 0

    private lateinit var stepLabel: android.widget.TextView
    private lateinit var grantAccessibilityBtn: Button
    private lateinit var grantBatteryBtn: Button
    private lateinit var checkLangBtn: Button
    private lateinit var phraseLayout: TextInputLayout
    private lateinit var phraseInput: TextInputEditText
    private lateinit var openaiLayout: TextInputLayout
    private lateinit var openaiInput: TextInputEditText
    private lateinit var scanQrBtn: Button
    private lateinit var continueBtn: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_onboarding)

        stepLabel = findViewById(R.id.onboarding_step_label)
        grantAccessibilityBtn = findViewById(R.id.onboarding_grant_accessibility_btn)
        grantBatteryBtn = findViewById(R.id.onboarding_grant_battery_btn)
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
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")),
                )
            } catch (_: ActivityNotFoundException) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }
        checkLangBtn.setOnClickListener {
            startActivity(Intent(Settings.ACTION_VOICE_INPUT_SETTINGS))
        }
        scanQrBtn.setOnClickListener {
            startActivity(Intent(this, PairingActivity::class.java))
        }
        continueBtn.setOnClickListener { advanceStep() }

        phraseInput.setText(BenSecrets.wakePhrase(this))
        renderStep()
    }

    private fun renderStep() {
        when (step) {
            0 -> {
                stepLabel.text = getString(R.string.onboarding_step_perms)
                grantAccessibilityBtn.visibility = View.VISIBLE
                grantBatteryBtn.visibility = View.VISIBLE
                checkLangBtn.visibility = View.VISIBLE
                phraseLayout.visibility = View.GONE
                openaiLayout.visibility = View.GONE
                scanQrBtn.visibility = View.GONE
                continueBtn.text = getString(R.string.onboarding_continue)
            }
            1 -> {
                stepLabel.text = getString(R.string.onboarding_step_keys)
                grantAccessibilityBtn.visibility = View.GONE
                grantBatteryBtn.visibility = View.GONE
                checkLangBtn.visibility = View.GONE
                phraseLayout.visibility = View.VISIBLE
                openaiLayout.visibility = View.VISIBLE
                scanQrBtn.visibility = View.GONE
                continueBtn.text = getString(R.string.onboarding_continue)
            }
            2 -> {
                stepLabel.text = getString(R.string.onboarding_step_pair)
                grantAccessibilityBtn.visibility = View.GONE
                grantBatteryBtn.visibility = View.GONE
                checkLangBtn.visibility = View.GONE
                phraseLayout.visibility = View.GONE
                openaiLayout.visibility = View.GONE
                scanQrBtn.visibility = View.VISIBLE
                continueBtn.text = getString(R.string.onboarding_finish)
            }
        }
    }

    private fun advanceStep() {
        when (step) {
            0 -> { step = 1; renderStep() }
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
                // Now (and only now) is it safe to boot the always-on stack:
                // we have an OpenAI key, a wake phrase, and (optionally) a peer.
                BenForegroundService.startIfNeeded(this)
                startActivity(Intent(this, MainActivity::class.java))
                finish()
            }
        }
    }
}
