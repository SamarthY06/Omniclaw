package com.ben.permission

import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/**
 * Tiny transparent activity that requests one or more runtime permissions
 * on behalf of an Android device tool that returned permission_not_granted.
 *
 * Flow:
 *   1. AndroidDeviceBridge.<tool> returns { ok:false, error:"permission_not_granted" } AND
 *      fires an Intent at this activity with EXTRA_PERMISSIONS = String[].
 *   2. The Realtime model receives the error, says something like "I tried
 *      to read your contacts but I don't have permission. Could you allow it
 *      in the dialog that just popped up and ask me again?"
 *   3. The user taps Allow on the system permission dialog this activity
 *      shows. Activity finishes with no UI footprint.
 *   4. The user repeats the voice command; the tool now succeeds.
 *
 * Why a separate activity instead of an in-line dialog from a service:
 * Android's runtime permission API (RequestMultiplePermissions) requires an
 * Activity host. Services can't request permissions directly.
 *
 * Theme is intentionally translucent so this activity doesn't visibly
 * interrupt whatever the user is currently looking at - they just see the
 * system permission dialog appear over their current screen.
 */
class PermissionGateActivity : AppCompatActivity() {
    private val tag = "PermissionGate"

    private lateinit var launcher: ActivityResultLauncher<Array<String>>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // No setContentView - we're a translucent shim activity.
        launcher = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
            results.forEach { (perm, granted) ->
                Log.i(tag, "permission $perm => ${if (granted) "GRANTED" else "DENIED"}")
            }
            finish()
        }
        val perms = intent.getStringArrayExtra(EXTRA_PERMISSIONS)
        if (perms.isNullOrEmpty()) {
            Log.w(tag, "started with empty EXTRA_PERMISSIONS; finishing")
            finish()
            return
        }
        // Filter out anything already granted so we don't re-prompt unnecessarily.
        val needed = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }.toTypedArray()
        if (needed.isEmpty()) {
            Log.i(tag, "all permissions already granted; finishing")
            finish()
            return
        }
        Log.i(tag, "requesting permissions: ${needed.joinToString(",")}")
        launcher.launch(needed)
    }

    companion object {
        const val EXTRA_PERMISSIONS = "com.ben.permission.EXTRA_PERMISSIONS"
    }
}
