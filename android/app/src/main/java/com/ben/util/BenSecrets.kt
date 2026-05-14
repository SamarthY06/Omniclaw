package com.ben.util

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * EncryptedSharedPreferences wrapper for Ben.
 *
 * Stores: OpenAI API key, peer shared secret, wake phrase, schedule, etc.
 * Backed by Android Keystore via the Jetpack Security library so even root-on-debug
 * can't trivially read these (without unlocking the device).
 */
object BenSecrets {
    private const val FILE_NAME = "ben_secrets"
    private const val KEY_ONBOARDING_DONE = "onboarding_done"
    private const val KEY_OPENAI = "openai_api_key"
    private const val KEY_WAKE_PHRASE = "wake_phrase"
    private const val KEY_WAKE_SCHEDULE = "wake_schedule"
    private const val KEY_PEER_SECRET_B64 = "peer_secret_b64"
    private const val KEY_PEER_DEVICE_ID = "peer_device_id"
    private const val KEY_PEER_HOST = "peer_host"
    private const val KEY_PEER_PORT = "peer_port"
    private const val KEY_OWN_DEVICE_ID = "own_device_id"
    private const val KEY_STORE_AUDIO = "store_audio"

    // Cost ledger - persisted because we must survive process death and
    // reboots without losing accounting. Stored as the raw USD value times
    // 1e6 to keep them as Long (SharedPreferences has no double accessor).
    private const val KEY_COST_DAILY_USD_MICRO = "cost_daily_usd_micro"
    private const val KEY_COST_DAILY_DATE = "cost_daily_date"          // ISO yyyy-MM-dd
    private const val KEY_COST_MONTHLY_USD_MICRO = "cost_monthly_usd_micro"
    private const val KEY_COST_MONTHLY_KEY = "cost_monthly_key"        // ISO yyyy-MM
    private const val KEY_COST_DAILY_CAP_USD_MICRO = "cost_daily_cap_usd_micro"
    private const val KEY_COST_MONTHLY_CAP_USD_MICRO = "cost_monthly_cap_usd_micro"

    private fun prefs(ctx: Context): SharedPreferences {
        val masterKey = MasterKey.Builder(ctx)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        return EncryptedSharedPreferences.create(
            ctx,
            FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    fun isOnboardingComplete(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_ONBOARDING_DONE, false)
    fun setOnboardingComplete(ctx: Context, done: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_ONBOARDING_DONE, done).apply()
    }

    fun openaiKey(ctx: Context): String? = prefs(ctx).getString(KEY_OPENAI, null)
    fun setOpenaiKey(ctx: Context, value: String) {
        prefs(ctx).edit().putString(KEY_OPENAI, value).apply()
    }

    fun wakePhrase(ctx: Context): String = prefs(ctx).getString(KEY_WAKE_PHRASE, "Ben") ?: "Ben"
    fun setWakePhrase(ctx: Context, value: String) {
        prefs(ctx).edit().putString(KEY_WAKE_PHRASE, value).apply()
    }

    fun wakeSchedule(ctx: Context): String? = prefs(ctx).getString(KEY_WAKE_SCHEDULE, null)
    fun setWakeSchedule(ctx: Context, value: String?) {
        prefs(ctx).edit().putString(KEY_WAKE_SCHEDULE, value).apply()
    }

    fun peerSecretB64(ctx: Context): String? = prefs(ctx).getString(KEY_PEER_SECRET_B64, null)
    fun peerDeviceId(ctx: Context): String? = prefs(ctx).getString(KEY_PEER_DEVICE_ID, null)
    fun peerHost(ctx: Context): String? = prefs(ctx).getString(KEY_PEER_HOST, null)
    fun peerPort(ctx: Context): Int = prefs(ctx).getInt(KEY_PEER_PORT, 18790)
    fun ownDeviceId(ctx: Context): String {
        val existing = prefs(ctx).getString(KEY_OWN_DEVICE_ID, null)
        if (existing != null) return existing
        val fresh = "android-${java.util.UUID.randomUUID()}"
        prefs(ctx).edit().putString(KEY_OWN_DEVICE_ID, fresh).apply()
        return fresh
    }

    fun setPeer(ctx: Context, deviceId: String, host: String, port: Int, secretB64: String) {
        prefs(ctx).edit()
            .putString(KEY_PEER_DEVICE_ID, deviceId)
            .putString(KEY_PEER_HOST, host)
            .putInt(KEY_PEER_PORT, port)
            .putString(KEY_PEER_SECRET_B64, secretB64)
            .apply()
    }

    fun storeAudio(ctx: Context): Boolean = prefs(ctx).getBoolean(KEY_STORE_AUDIO, false)
    fun setStoreAudio(ctx: Context, value: Boolean) {
        prefs(ctx).edit().putBoolean(KEY_STORE_AUDIO, value).apply()
    }

    // ---- Cost ledger storage --------------------------------------------
    // All values stored as USD * 1_000_000 (micro-dollars) so we can use
    // putLong / getLong without floating-point loss in EncryptedSharedPrefs.

    private fun toMicroUsd(usd: Double): Long = (usd * 1_000_000).toLong()
    private fun fromMicroUsd(micro: Long): Double = micro / 1_000_000.0

    fun costDailyUsd(ctx: Context): Double = fromMicroUsd(prefs(ctx).getLong(KEY_COST_DAILY_USD_MICRO, 0L))
    fun costDailyDate(ctx: Context): String? = prefs(ctx).getString(KEY_COST_DAILY_DATE, null)
    fun costMonthlyUsd(ctx: Context): Double = fromMicroUsd(prefs(ctx).getLong(KEY_COST_MONTHLY_USD_MICRO, 0L))
    fun costMonthlyKey(ctx: Context): String? = prefs(ctx).getString(KEY_COST_MONTHLY_KEY, null)

    fun setCostDaily(ctx: Context, usd: Double, date: String) {
        prefs(ctx).edit()
            .putLong(KEY_COST_DAILY_USD_MICRO, toMicroUsd(usd))
            .putString(KEY_COST_DAILY_DATE, date)
            .apply()
    }

    fun setCostMonthly(ctx: Context, usd: Double, monthKey: String) {
        prefs(ctx).edit()
            .putLong(KEY_COST_MONTHLY_USD_MICRO, toMicroUsd(usd))
            .putString(KEY_COST_MONTHLY_KEY, monthKey)
            .apply()
    }

    fun dailyCapUsd(ctx: Context, fallback: Double): Double {
        val raw = prefs(ctx).getLong(KEY_COST_DAILY_CAP_USD_MICRO, -1L)
        return if (raw < 0L) fallback else fromMicroUsd(raw)
    }

    fun setDailyCapUsd(ctx: Context, usd: Double) {
        prefs(ctx).edit().putLong(KEY_COST_DAILY_CAP_USD_MICRO, toMicroUsd(usd)).apply()
    }

    fun monthlyCapUsd(ctx: Context, fallback: Double): Double {
        val raw = prefs(ctx).getLong(KEY_COST_MONTHLY_CAP_USD_MICRO, -1L)
        return if (raw < 0L) fallback else fromMicroUsd(raw)
    }

    fun setMonthlyCapUsd(ctx: Context, usd: Double) {
        prefs(ctx).edit().putLong(KEY_COST_MONTHLY_CAP_USD_MICRO, toMicroUsd(usd)).apply()
    }
}
