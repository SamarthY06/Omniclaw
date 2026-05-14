package com.ben.service

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.ben.MainActivity
import com.ben.R
import com.ben.util.BenSecrets

/**
 * Always-on host service. Owns:
 *   * the Android-side notification (visible to keep the OS happy),
 *   * the [NodeBridgeService] lifecycle (starts the embedded Node runtime),
 *   * the [BenWakewordService] lifecycle (keeps the SpeechRecognizer loop alive).
 *
 * BenVoiceService is started on demand by BenWakewordService when the wake
 * phrase fires, then kills itself again after 180s of silence.
 */
class BenForegroundService : Service() {
    private val tag = "BenForegroundService"

    override fun onCreate() {
        super.onCreate()
        // Android 14 (API 34) made the 2-arg startForeground throw
        // ForegroundServiceTypeException for any service that declared a
        // foregroundServiceType in the manifest. Our manifest declares
        // microphone|specialUse for this service, so we MUST pass those
        // exact bits via the 3-arg overload on Q+. On older OS versions the
        // 3-arg overload didn't exist; fall back to the 2-arg one.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val typeBits = ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            try {
                startForeground(NOTIFICATION_ID, buildNotification(idle = true), typeBits)
            } catch (e: Exception) {
                // Some OEM ROMs reject specialUse - retry with microphone only.
                // We always need MICROPHONE for the wake-word path; SPECIAL_USE
                // is only the catch-all that documents what specialUse means.
                Log.w(tag, "startForeground(MIC|SPECIAL_USE) failed; retrying MIC only", e)
                startForeground(
                    NOTIFICATION_ID,
                    buildNotification(idle = true),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
                )
            }
        } else {
            @Suppress("DEPRECATION")
            startForeground(NOTIFICATION_ID, buildNotification(idle = true))
        }
        startService(Intent(this, NodeBridgeService::class.java))
        startService(Intent(this, BenWakewordService::class.java))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_SET_ACTIVE -> updateNotification(idle = false)
            ACTION_SET_IDLE -> updateNotification(idle = true)
            ACTION_REFRESH_NOTIFICATION -> updateNotification(idle = true)
            ACTION_STOP_ALL -> {
                Log.i(tag, "Stop button pressed - tearing down all Ben services")
                stopChildren()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        stopChildren()
    }

    private fun stopChildren() {
        try { stopService(Intent(this, BenVoiceService::class.java)) } catch (_: Exception) {}
        try { stopService(Intent(this, BenWakewordService::class.java)) } catch (_: Exception) {}
        try { stopService(Intent(this, NodeBridgeService::class.java)) } catch (_: Exception) {}
    }

    private fun buildNotification(idle: Boolean): Notification {
        val tapPi = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stopIntent = Intent(this, BenForegroundService::class.java).setAction(ACTION_STOP_ALL)
        val stopPi = PendingIntent.getService(
            this,
            1,
            stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // Idle text interpolates the live wake phrase from BenSecrets so the
        // user sees Listening for "Sasha" if they configured a custom phrase
        // (it used to be hardcoded to "Ben" via strings.xml).
        val text = if (idle) {
            getString(R.string.notif_text_idle, BenSecrets.wakePhrase(this))
        } else {
            getString(R.string.notif_text_active)
        }
        return NotificationCompat.Builder(this, getString(R.string.notif_channel_id))
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(tapPi)
            .addAction(0, getString(R.string.notif_action_stop), stopPi)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun updateNotification(idle: Boolean) {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        nm.notify(NOTIFICATION_ID, buildNotification(idle))
    }

    companion object {
        private const val NOTIFICATION_ID = 4711
        const val ACTION_SET_ACTIVE = "com.ben.action.SET_ACTIVE"
        const val ACTION_SET_IDLE = "com.ben.action.SET_IDLE"
        const val ACTION_STOP_ALL = "com.ben.action.STOP_ALL"
        const val ACTION_REFRESH_NOTIFICATION = "com.ben.action.REFRESH_NOTIFICATION"

        fun startIfNeeded(ctx: Context) {
            val intent = Intent(ctx, BenForegroundService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(intent) else ctx.startService(intent)
        }

        fun setActive(ctx: Context, active: Boolean) {
            val intent = Intent(ctx, BenForegroundService::class.java).apply {
                action = if (active) ACTION_SET_ACTIVE else ACTION_SET_IDLE
            }
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(intent) else ctx.startService(intent)
            } catch (e: Exception) {
                Log.w("BenForegroundService", "setActive failed", e)
            }
        }
    }
}
