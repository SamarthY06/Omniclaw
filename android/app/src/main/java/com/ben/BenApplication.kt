package com.ben

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.ben.service.BenForegroundService
import com.ben.util.BenSecrets

class BenApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        ensureNotificationChannel()
        // Only boot the always-on stack once onboarding has finished. Otherwise
        // the wake-word listener fires before the user has even pasted an
        // OpenAI key, which (combined with the device-locale recognizer) causes
        // a runaway feedback loop. OnboardingActivity calls
        // BenForegroundService.startIfNeeded(this) explicitly when the user
        // completes step 3.
        if (BenSecrets.isOnboardingComplete(this)) {
            BenForegroundService.startIfNeeded(this)
        }
    }

    private fun ensureNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        val channelId = getString(R.string.notif_channel_id)
        if (nm.getNotificationChannel(channelId) != null) return
        nm.createNotificationChannel(
            NotificationChannel(
                channelId,
                getString(R.string.notif_channel_name),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Always-on Ben listener and embedded agent."
                setShowBadge(false)
            },
        )
    }
}
