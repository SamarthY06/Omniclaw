package com.ben.settings

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.localbroadcastmanager.content.LocalBroadcastManager
import com.ben.R
import com.ben.pairing.PairingActivity
import com.ben.service.BenForegroundService
import com.ben.service.BenWakewordService
import com.ben.ui.HomeFragment
import com.ben.util.BenSecrets
import com.google.android.material.button.MaterialButton
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.TextInputEditText

class SettingsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)

        val key = findViewById<TextInputEditText>(R.id.settings_openai_key)
        val phrase = findViewById<TextInputEditText>(R.id.settings_wake_phrase)
        val schedule = findViewById<TextInputEditText>(R.id.settings_wake_schedule)
        val storeAudio = findViewById<MaterialSwitch>(R.id.settings_store_audio)
        val saveBtn = findViewById<MaterialButton>(R.id.settings_save_btn)
        val repairBtn = findViewById<MaterialButton>(R.id.settings_repair_btn)
        val micTestBtn = findViewById<MaterialButton>(R.id.settings_mic_test_btn)
        val peerStatus = findViewById<android.widget.TextView>(R.id.settings_peer_status)

        key.setText(BenSecrets.openaiKey(this).orEmpty())
        phrase.setText(BenSecrets.wakePhrase(this))
        schedule.setText(BenSecrets.wakeSchedule(this).orEmpty())
        storeAudio.isChecked = BenSecrets.storeAudio(this)
        peerStatus.text = "Peer: ${BenSecrets.peerHost(this) ?: "not paired"}"

        saveBtn.setOnClickListener {
            val k = key.text?.toString()?.trim().orEmpty()
            if (k.isNotBlank()) BenSecrets.setOpenaiKey(this, k)
            val previousPhrase = BenSecrets.wakePhrase(this)
            val p = phrase.text?.toString()?.trim().orEmpty()
            val phraseChanged = p.isNotBlank() && p != previousPhrase
            if (p.isNotBlank()) BenSecrets.setWakePhrase(this, p)
            BenSecrets.setWakeSchedule(this, schedule.text?.toString()?.trim())
            BenSecrets.setStoreAudio(this, storeAudio.isChecked)
            // Tell the wake-word service to reload its phrase.
            startService(Intent(this, BenWakewordService::class.java).setAction(BenWakewordService.ACTION_RELOAD_PHRASE))
            if (phraseChanged) {
                // Refresh the host notification so the lock-screen text
                // shows the new phrase too.
                startService(Intent(this, BenForegroundService::class.java).setAction(BenForegroundService.ACTION_REFRESH_NOTIFICATION))
                // In-process broadcast so HomeFragment re-renders without
                // waiting for its next onResume.
                LocalBroadcastManager.getInstance(this)
                    .sendBroadcast(Intent(HomeFragment.ACTION_WAKE_PHRASE_CHANGED))
            }
            finish()
        }

        repairBtn.setOnClickListener {
            startActivity(Intent(this, PairingActivity::class.java))
        }

        micTestBtn.setOnClickListener {
            startActivity(Intent(this, com.ben.MainActivity::class.java))
        }
    }
}
