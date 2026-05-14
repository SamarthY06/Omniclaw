package com.ben.util

import android.content.Context
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicReference

/**
 * Daily + monthly OpenAI spend tracker, with a hard refusal flow when the
 * configured cap is exceeded.
 *
 * Why a ledger and not a token bucket: the user configured caps in USD,
 * not in API requests. Token-bucket rate-limiting is the wrong tool for a
 * "I don't want to spend more than $5 a day" requirement - it doesn't
 * compose across the heterogeneous Realtime / vision / chat call kinds
 * which all have different token costs.
 *
 * Why persist at all: the foreground service can be killed and restarted
 * mid-day; if we kept the ledger in memory only, every kill would reset
 * the daily counter and let the user accidentally blow past the cap.
 *
 * Refusal flow (called from BenVoiceService before every WSS open and
 * before every tool dispatch that has measurable token cost):
 *   1. recordOrRefuse(kind, inputTokens, outputTokens) updates the running
 *      daily / monthly totals.
 *   2. If after the update the cap is exceeded, we DON'T undo the charge
 *      (it really was spent), but we set a "refused" flag for the next
 *      session start and append a system note to the ongoing conversation
 *      so the model can apologise and end the session.
 *
 * Caps are read from BenSecrets so the user can adjust them via Settings
 * without rebuilding the APK. Defaults live in OpenAiConfig.
 *
 * Date / month rollover happens lazily on the first record() of a new day
 * (cheap; a String comparison on a 10-char date).
 */
object CostLedger {
    private const val TAG = "CostLedger"

    /**
     * Set by recordOrRefuse(...) when a charge crosses the cap. The next
     * BenVoiceService session start checks this and refuses the session
     * before opening the WSS, so the user gets one apology rather than an
     * apology after every turn until midnight.
     */
    private val refusedReason = AtomicReference<RefusalReason?>(null)

    enum class RefusalReason(val userMessage: String) {
        DAILY_CAP_EXCEEDED(
            "You've reached today's spending cap on Ben. Open the app's Settings -> Cost cap to raise it, or wait until tomorrow.",
        ),
        MONTHLY_CAP_EXCEEDED(
            "You've reached this month's spending cap on Ben. Open the app's Settings -> Cost cap to raise it, or wait until next month.",
        ),
    }

    /**
     * Aggregate the current ledger state. Useful for the Settings screen
     * and for the model's context (the system prompt can include a
     * "you've spent $X.YZ today" note so the model is aware).
     */
    data class Snapshot(
        val dailyUsd: Double,
        val dailyCapUsd: Double,
        val dailyDate: String,
        val monthlyUsd: Double,
        val monthlyCapUsd: Double,
        val monthlyKey: String,
        val refused: RefusalReason?,
    )

    fun snapshot(ctx: Context): Snapshot {
        val today = today()
        val month = thisMonth()
        rollIfNeeded(ctx, today, month)
        return Snapshot(
            dailyUsd = BenSecrets.costDailyUsd(ctx),
            dailyCapUsd = BenSecrets.dailyCapUsd(ctx, OpenAiConfig.DEFAULT_DAILY_CAP_USD),
            dailyDate = BenSecrets.costDailyDate(ctx) ?: today,
            monthlyUsd = BenSecrets.costMonthlyUsd(ctx),
            monthlyCapUsd = BenSecrets.monthlyCapUsd(ctx, OpenAiConfig.DEFAULT_MONTHLY_CAP_USD),
            monthlyKey = BenSecrets.costMonthlyKey(ctx) ?: month,
            refused = refusedReason.get(),
        )
    }

    /**
     * Check whether we should refuse to start a NEW session. Called from
     * BenVoiceService.connect() before we open the WSS. The model is told
     * via the system prompt how to apologise if this returns non-null.
     */
    fun checkRefusal(ctx: Context): RefusalReason? {
        val snap = snapshot(ctx)
        val cached = refusedReason.get()
        if (cached != null) return cached
        if (snap.dailyUsd >= snap.dailyCapUsd) {
            refusedReason.set(RefusalReason.DAILY_CAP_EXCEEDED)
            return RefusalReason.DAILY_CAP_EXCEEDED
        }
        if (snap.monthlyUsd >= snap.monthlyCapUsd) {
            refusedReason.set(RefusalReason.MONTHLY_CAP_EXCEEDED)
            return RefusalReason.MONTHLY_CAP_EXCEEDED
        }
        return null
    }

    /**
     * Cleared automatically on the first record() of a new day / month
     * (rollIfNeeded). Exposed so Settings screen / cap-raise action can
     * unblock the user without waiting for midnight.
     */
    fun clearRefusal() { refusedReason.set(null) }

    /**
     * Record one usage event (input + output tokens) under the appropriate
     * accounting bucket. Updates the daily / monthly running totals
     * atomically (single SharedPreferences edit per call). Returns the
     * incremental cost in USD for caller-side logging.
     *
     * Thread-safety: SharedPreferences is documented thread-safe. The
     * AtomicReference around refusedReason is the only cross-thread state.
     */
    fun record(ctx: Context, kind: CallKind, inputTokens: Long, outputTokens: Long): Double {
        val price = OpenAiConfig.COST_PRICES_USD[kind.accountingBucket] ?: run {
            Log.w(TAG, "no price row for bucket=${kind.accountingBucket}; recording 0")
            return 0.0
        }
        val incremental = price.cost(inputTokens, outputTokens)
        if (incremental <= 0.0) return 0.0

        val today = today()
        val month = thisMonth()
        rollIfNeeded(ctx, today, month)

        val newDaily = BenSecrets.costDailyUsd(ctx) + incremental
        BenSecrets.setCostDaily(ctx, newDaily, today)
        val newMonthly = BenSecrets.costMonthlyUsd(ctx) + incremental
        BenSecrets.setCostMonthly(ctx, newMonthly, month)

        val dailyCap = BenSecrets.dailyCapUsd(ctx, OpenAiConfig.DEFAULT_DAILY_CAP_USD)
        val monthlyCap = BenSecrets.monthlyCapUsd(ctx, OpenAiConfig.DEFAULT_MONTHLY_CAP_USD)
        if (newDaily >= dailyCap) refusedReason.compareAndSet(null, RefusalReason.DAILY_CAP_EXCEEDED)
        if (newMonthly >= monthlyCap) refusedReason.compareAndSet(null, RefusalReason.MONTHLY_CAP_EXCEEDED)

        Log.d(
            TAG,
            "record kind=$kind in=$inputTokens out=$outputTokens cost=$%.6f daily=$%.4f/$%.2f monthly=$%.4f/$%.2f"
                .format(incremental, newDaily, dailyCap, newMonthly, monthlyCap),
        )
        return incremental
    }

    /**
     * Roll the daily / monthly counters when the date crosses midnight or
     * the month changes. Cheap (string compare) and safe to call before
     * every record().
     */
    private fun rollIfNeeded(ctx: Context, today: String, month: String) {
        val storedDate = BenSecrets.costDailyDate(ctx)
        if (storedDate == null) {
            BenSecrets.setCostDaily(ctx, 0.0, today)
        } else if (storedDate != today) {
            Log.i(TAG, "daily rollover: $storedDate -> $today (resetting daily counter)")
            BenSecrets.setCostDaily(ctx, 0.0, today)
            // Daily rollover also clears the daily-cap refusal. Monthly
            // refusal stays in place until the month rolls over.
            if (refusedReason.get() == RefusalReason.DAILY_CAP_EXCEEDED) {
                refusedReason.set(null)
            }
        }

        val storedMonth = BenSecrets.costMonthlyKey(ctx)
        if (storedMonth == null) {
            BenSecrets.setCostMonthly(ctx, 0.0, month)
        } else if (storedMonth != month) {
            Log.i(TAG, "monthly rollover: $storedMonth -> $month (resetting monthly counter)")
            BenSecrets.setCostMonthly(ctx, 0.0, month)
            refusedReason.set(null)
        }
    }

    private fun today(): String =
        SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())

    private fun thisMonth(): String =
        SimpleDateFormat("yyyy-MM", Locale.US).format(Date())
}
