package com.ben.bridge

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Looper
import android.provider.ContactsContract
import android.util.Log
import androidx.core.content.ContextCompat
import com.ben.permission.PermissionGateActivity
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Native Android device-API tools surfaced to the Realtime model via the
 * Node bridge. Each public method here is wired into NodeBridgeService's
 * RPC handler map under a `device.*` name; the OpenClaw tool registry
 * exposes those names to the model.
 *
 * Permission UX: when a tool is invoked but its permission isn't granted,
 * we return { ok:false, error:"permission_not_granted", permission:<perm> }
 * AND fire an intent at PermissionGateActivity. The activity prompts the
 * user; on grant the user can re-issue the voice command and the tool
 * succeeds. We deliberately do NOT block here waiting for the user - that
 * would freeze the conversation thread.
 *
 * All methods return JSONObject so the upstream JSON-RPC server can serialise
 * uniformly.
 */
object AndroidDeviceBridge {
    private const val tag = "AndroidDeviceBridge"

    // ---------- LOCATION ----------
    fun getLocation(ctx: Context, args: JSONObject): JSONObject {
        if (!hasPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) &&
            !hasPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION)) {
            requestPermissions(ctx, arrayOf(
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION,
            ))
            return permissionDenied(Manifest.permission.ACCESS_FINE_LOCATION,
                "Location permission isn't granted yet. The user has been prompted; ask them to allow it and retry.")
        }
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
            ?: return errorResult("location_service_unavailable")
        val high = args.optBoolean("high_accuracy", false)
        val location = lastKnownLocation(lm, high)
            ?: return errorResult("no_fix_available", "No recent GPS / Network location fix on this device. Have the user move outdoors or wait a few seconds and retry.")
        return JSONObject()
            .put("ok", true)
            .put("result", JSONObject()
                .put("latitude", location.latitude)
                .put("longitude", location.longitude)
                .put("accuracy_m", location.accuracy.toDouble())
                .put("source", location.provider ?: "unknown")
                .put("age_ms", System.currentTimeMillis() - location.time))
    }

    private fun lastKnownLocation(lm: LocationManager, preferGps: Boolean): Location? {
        val providers = lm.getProviders(true)
        val ordered = if (preferGps) {
            listOf(LocationManager.GPS_PROVIDER, LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER)
        } else {
            listOf(LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
        }
        for (p in ordered) {
            if (!providers.contains(p)) continue
            try {
                val loc = lm.getLastKnownLocation(p) ?: continue
                return loc
            } catch (e: SecurityException) {
                Log.w(tag, "lastKnownLocation: SecurityException for $p", e)
            } catch (e: Exception) {
                Log.w(tag, "lastKnownLocation: $p failed: ${e.message}")
            }
        }
        return null
    }

    // ---------- CONTACTS ----------
    fun getContacts(ctx: Context, args: JSONObject): JSONObject {
        if (!hasPermission(ctx, Manifest.permission.READ_CONTACTS)) {
            requestPermissions(ctx, arrayOf(Manifest.permission.READ_CONTACTS))
            return permissionDenied(Manifest.permission.READ_CONTACTS,
                "Contacts permission isn't granted yet. The user has been prompted; ask them to allow and retry.")
        }
        val query = args.optString("query", "").trim()
        val limit = args.optInt("limit", 25).coerceIn(1, 100)
        val resolver = ctx.contentResolver

        val out = JSONArray()
        val baseUri = ContactsContract.Contacts.CONTENT_URI
        val (selection, selectionArgs) = if (query.isEmpty()) Pair(null, null)
            else Pair(
                "${ContactsContract.Contacts.DISPLAY_NAME_PRIMARY} LIKE ?",
                arrayOf("%$query%"),
            )
        val cursor = try {
            resolver.query(
                baseUri,
                arrayOf(
                    ContactsContract.Contacts._ID,
                    ContactsContract.Contacts.DISPLAY_NAME_PRIMARY,
                    ContactsContract.Contacts.HAS_PHONE_NUMBER,
                ),
                selection,
                selectionArgs,
                "${ContactsContract.Contacts.DISPLAY_NAME_PRIMARY} COLLATE NOCASE ASC",
            )
        } catch (e: Exception) {
            return errorResult("contacts_query_failed", e.message ?: "")
        } ?: return errorResult("contacts_query_returned_null")

        cursor.use { c ->
            var taken = 0
            while (c.moveToNext() && taken < limit) {
                val id = c.getLong(c.getColumnIndexOrThrow(ContactsContract.Contacts._ID))
                val name = c.getString(c.getColumnIndexOrThrow(ContactsContract.Contacts.DISPLAY_NAME_PRIMARY)) ?: continue
                val hasPhone = c.getInt(c.getColumnIndexOrThrow(ContactsContract.Contacts.HAS_PHONE_NUMBER)) > 0
                val phones = if (hasPhone) lookupPhones(ctx, id) else JSONArray()
                val emails = lookupEmails(ctx, id)
                out.put(JSONObject()
                    .put("id", id)
                    .put("name", name)
                    .put("phones", phones)
                    .put("emails", emails))
                taken++
            }
        }
        return JSONObject()
            .put("ok", true)
            .put("result", JSONObject().put("contacts", out).put("count", out.length()))
    }

    private fun lookupPhones(ctx: Context, contactId: Long): JSONArray {
        val arr = JSONArray()
        val cur = try {
            ctx.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(ContactsContract.CommonDataKinds.Phone.NUMBER),
                "${ContactsContract.CommonDataKinds.Phone.CONTACT_ID} = ?",
                arrayOf(contactId.toString()),
                null,
            )
        } catch (e: Exception) { null } ?: return arr
        cur.use { c ->
            while (c.moveToNext()) {
                val num = c.getString(0) ?: continue
                arr.put(num)
            }
        }
        return arr
    }

    private fun lookupEmails(ctx: Context, contactId: Long): JSONArray {
        val arr = JSONArray()
        val cur = try {
            ctx.contentResolver.query(
                ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                arrayOf(ContactsContract.CommonDataKinds.Email.ADDRESS),
                "${ContactsContract.CommonDataKinds.Email.CONTACT_ID} = ?",
                arrayOf(contactId.toString()),
                null,
            )
        } catch (e: Exception) { null } ?: return arr
        cur.use { c ->
            while (c.moveToNext()) {
                val addr = c.getString(0) ?: continue
                arr.put(addr)
            }
        }
        return arr
    }

    // ---------- CALL ----------
    fun placeCall(ctx: Context, args: JSONObject): JSONObject {
        if (!hasPermission(ctx, Manifest.permission.CALL_PHONE)) {
            requestPermissions(ctx, arrayOf(Manifest.permission.CALL_PHONE))
            return permissionDenied(Manifest.permission.CALL_PHONE,
                "Call permission isn't granted yet. The user has been prompted; ask them to allow and retry.")
        }
        val literalNumber = args.optString("number", "").trim()
        val contactName = args.optString("contact_name", "").trim()
        val number = if (literalNumber.isNotEmpty()) literalNumber else {
            if (contactName.isEmpty()) return errorResult("number_or_contact_name_required")
            // Must also have READ_CONTACTS to resolve a name.
            if (!hasPermission(ctx, Manifest.permission.READ_CONTACTS)) {
                requestPermissions(ctx, arrayOf(Manifest.permission.READ_CONTACTS))
                return permissionDenied(Manifest.permission.READ_CONTACTS,
                    "Contacts permission needed to resolve $contactName by name.")
            }
            val resolved = resolveContactToNumber(ctx, contactName)
                ?: return errorResult("contact_not_found", "No contact matched \"$contactName\".")
            resolved
        }
        return try {
            val intent = Intent(Intent.ACTION_CALL).apply {
                data = Uri.fromParts("tel", number, null)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            ctx.startActivity(intent)
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("dialed", number)
                .put("contact_name", contactName.ifBlank { JSONObject.NULL }))
        } catch (e: SecurityException) {
            errorResult("call_security", e.message ?: "")
        } catch (e: Exception) {
            errorResult("call_failed", e.message ?: "")
        }
    }

    private fun resolveContactToNumber(ctx: Context, query: String): String? {
        // Pick the most-recently-contacted person matching the substring.
        val cur = try {
            ctx.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER),
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ?",
                arrayOf("%$query%"),
                "${ContactsContract.Contacts.LAST_TIME_CONTACTED} DESC",
            )
        } catch (_: Exception) { null } ?: return null
        cur.use { c -> if (c.moveToFirst()) return c.getString(1) }
        return null
    }

    // ---------- LAUNCH APP ----------
    fun launchApp(ctx: Context, args: JSONObject): JSONObject {
        val pkg = args.optString("package", "").trim()
        val label = args.optString("label", "").trim()
        val pm = ctx.packageManager
        val resolved = when {
            pkg.isNotEmpty() -> pkg
            label.isNotEmpty() -> resolvePackageByLabel(pm, label)
                ?: return errorResult("app_not_found", "No installed app matched label \"$label\".")
            else -> return errorResult("package_or_label_required")
        }
        val intent = pm.getLaunchIntentForPackage(resolved)
            ?: return errorResult("no_launch_intent", "$resolved is installed but has no launch intent (e.g. it's a service-only package).")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        return try {
            ctx.startActivity(intent)
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("launched", true)
                .put("package", resolved))
        } catch (e: Exception) {
            errorResult("launch_failed", e.message ?: "")
        }
    }

    private fun resolvePackageByLabel(pm: PackageManager, label: String): String? {
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val candidates = pm.queryIntentActivities(main, 0)
        val needle = label.lowercase()
        // Exact, then prefix, then substring.
        var exact: String? = null; var prefix: String? = null; var sub: String? = null
        for (ri in candidates) {
            val l = ri.loadLabel(pm)?.toString()?.lowercase() ?: continue
            val pkg = ri.activityInfo?.packageName ?: continue
            if (l == needle && exact == null) exact = pkg
            else if (l.startsWith(needle) && prefix == null) prefix = pkg
            else if (l.contains(needle) && sub == null) sub = pkg
        }
        return exact ?: prefix ?: sub
    }

    // ---------- CLIPBOARD ----------
    fun clipboardGet(ctx: Context, @Suppress("UNUSED_PARAMETER") args: JSONObject): JSONObject {
        return runOnMain {
            val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                ?: return@runOnMain errorResult("clipboard_service_unavailable")
            val clip = cm.primaryClip
            val text = if (clip != null && clip.itemCount > 0) clip.getItemAt(0).coerceToText(ctx)?.toString() ?: "" else ""
            JSONObject().put("ok", true).put("result", JSONObject().put("text", text))
        }
    }

    fun clipboardSet(ctx: Context, args: JSONObject): JSONObject {
        val text = args.optString("text", "")
        return runOnMain {
            val cm = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                ?: return@runOnMain errorResult("clipboard_service_unavailable")
            cm.setPrimaryClip(ClipData.newPlainText("ben", text))
            JSONObject().put("ok", true).put("result", JSONObject().put("set", true).put("length", text.length))
        }
    }

    // ---------- BATTERY ----------
    fun batteryStatus(ctx: Context, @Suppress("UNUSED_PARAMETER") args: JSONObject): JSONObject {
        val bm = ctx.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
            ?: return errorResult("battery_service_unavailable")
        val percent = try { bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) } catch (_: Exception) { -1 }
        val status = try { bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_STATUS) } catch (_: Exception) { -1 }
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        // Plug type via sticky broadcast.
        val intent = ctx.registerReceiver(null, android.content.IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val plugged = intent?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val source = when (plugged) {
            BatteryManager.BATTERY_PLUGGED_AC -> "ac"
            BatteryManager.BATTERY_PLUGGED_USB -> "usb"
            BatteryManager.BATTERY_PLUGGED_WIRELESS -> "wireless"
            else -> null
        }
        val tempTenths = intent?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE
        val tempC = if (tempTenths != Int.MIN_VALUE) tempTenths / 10.0 else null
        val res = JSONObject().put("percent", percent).put("charging", charging)
        if (source != null) res.put("charging_source", source)
        if (tempC != null) res.put("temperature_c", tempC)
        return JSONObject().put("ok", true).put("result", res)
    }

    // ---------- helpers ----------
    private fun hasPermission(ctx: Context, perm: String): Boolean =
        ContextCompat.checkSelfPermission(ctx, perm) == PackageManager.PERMISSION_GRANTED

    private fun requestPermissions(ctx: Context, perms: Array<String>) {
        try {
            val intent = Intent(ctx, PermissionGateActivity::class.java).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                putExtra(PermissionGateActivity.EXTRA_PERMISSIONS, perms)
            }
            ctx.startActivity(intent)
        } catch (e: Exception) {
            Log.w(tag, "permission gate launch failed: ${e.message}")
        }
    }

    private fun permissionDenied(permission: String, hint: String): JSONObject =
        JSONObject()
            .put("ok", false)
            .put("error", "permission_not_granted")
            .put("permission", permission)
            .put("hint", hint)

    private fun errorResult(code: String, hint: String = ""): JSONObject {
        val o = JSONObject().put("ok", false).put("error", code)
        if (hint.isNotEmpty()) o.put("hint", hint)
        return o
    }

    /**
     * ClipboardManager calls must run on the main thread on some Android
     * builds (CTS-enforced on 13+). This helper bounces the work via the
     * Looper and waits up to 1 s; if we're already on the main thread we
     * just call inline.
     */
    private fun runOnMain(block: () -> JSONObject): JSONObject {
        if (Looper.myLooper() == Looper.getMainLooper()) return block()
        val latch = CountDownLatch(1)
        val ref = arrayOfNulls<JSONObject>(1)
        android.os.Handler(Looper.getMainLooper()).post {
            try { ref[0] = block() } catch (e: Exception) {
                ref[0] = errorResult("main_thread_exception", e.message ?: "")
            } finally { latch.countDown() }
        }
        latch.await(1500, TimeUnit.MILLISECONDS)
        return ref[0] ?: errorResult("main_thread_timeout")
    }
}
