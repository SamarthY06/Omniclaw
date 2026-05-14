package com.ben.bridge

import android.Manifest
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Address
import android.location.Geocoder
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Bundle
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
        // v0.1.7: request a FRESH fix instead of just blindly trusting
        // getLastKnownLocation. Pre-fix, on a Samsung that had a stale
        // last-known fix from some other app (e.g. an old Google Maps
        // query from a different city), our location result was
        // technically valid but factually wrong - "fake location" from
        // the user's POV. We now (a) request a single fresh update from
        // FUSED / GPS / NETWORK in parallel, (b) wait up to 4 s for the
        // first one that arrives, (c) fall back to last-known if all
        // providers time out, and (d) explicitly mark the result with
        // `freshness: "fresh"|"last_known"` and an age field so the
        // model can hedge ("your last known location, ~12 minutes old")
        // rather than confidently lie.
        var location = requestFreshLocation(lm, preferGps = high, timeoutMs = 4_000)
        var freshness = "fresh"
        if (location == null) {
            location = lastKnownLocation(lm, high)
            freshness = "last_known"
        }
        if (location == null) {
            return errorResult("no_fix_available", "No recent GPS / Network location fix on this device. Have the user move outdoors or wait a few seconds and retry.")
        }
        // Reverse-geocode so the model gets a human-readable place name to
        // recite, not just lat/lon. Without this, "where am I?" responses
        // came back as "I have coordinates 12.97, 77.59" which is useless
        // to the user. Geocoder.getFromLocation is synchronous on the
        // legacy API and async on API 33+; we use the legacy sync version
        // here because we're already off-main-thread.
        val ageMs = System.currentTimeMillis() - location.time
        val result = JSONObject()
            .put("latitude", location.latitude)
            .put("longitude", location.longitude)
            .put("accuracy_m", location.accuracy.toDouble())
            .put("source", location.provider ?: "unknown")
            .put("age_ms", ageMs)
            .put("freshness", freshness)
            .put("is_stale", freshness == "last_known" && ageMs > 5 * 60 * 1000L)
        try {
            if (Geocoder.isPresent()) {
                @Suppress("DEPRECATION")
                val addrs: List<Address>? = Geocoder(ctx).getFromLocation(location.latitude, location.longitude, 1)
                val a = addrs?.firstOrNull()
                if (a != null) {
                    val place = listOfNotNull(
                        a.subLocality,
                        a.locality,
                        a.subAdminArea,
                        a.adminArea,
                        a.countryName,
                    ).distinct().joinToString(", ")
                    if (a.locality != null) result.put("city", a.locality)
                    if (a.subAdminArea != null) result.put("district", a.subAdminArea)
                    if (a.adminArea != null) result.put("state", a.adminArea)
                    if (a.countryName != null) result.put("country", a.countryName)
                    if (a.countryCode != null) result.put("country_code", a.countryCode)
                    if (a.postalCode != null) result.put("postal_code", a.postalCode)
                    val line0 = a.getAddressLine(0)
                    if (!line0.isNullOrBlank()) result.put("full_address", line0)
                    if (place.isNotBlank()) result.put("place", place)
                    // The summary field is what the model should speak
                    // verbatim - one short, naturally-phrased sentence.
                    val summary = when {
                        a.locality != null && a.countryName != null -> "${a.locality}, ${a.countryName}"
                        a.subLocality != null && a.locality != null -> "${a.subLocality}, ${a.locality}"
                        place.isNotBlank() -> place
                        else -> "${location.latitude}, ${location.longitude}"
                    }
                    result.put("summary", summary)
                }
            }
        } catch (e: Exception) {
            // Reverse geocoding is best-effort; failure is fine, the model
            // still gets lat/lon and can use that for weather/maps tools.
            Log.w(tag, "reverse-geocode failed: ${e.message}")
        }
        return JSONObject().put("ok", true).put("result", result)
    }

    /**
     * Request a single fresh location update from each available provider
     * in parallel, return the first one that arrives within timeoutMs.
     * Falls back to null if nothing arrives in time (caller then uses
     * lastKnownLocation).
     *
     * Why this and not FusedLocationProviderClient: that one requires
     * Google Play Services. Most Samsungs have it but some Chinese
     * Androids (rooted MIUI / GrapheneOS) don't, and our APK is supposed
     * to not hard-depend on Play services. LocationManager's
     * requestSingleUpdate / requestLocationUpdates with the framework
     * providers (GPS / NETWORK / FUSED) is sufficient.
     */
    private fun requestFreshLocation(lm: LocationManager, preferGps: Boolean, timeoutMs: Long): Location? {
        val providers = lm.getProviders(true)
        val preferred = if (preferGps) {
            listOf(LocationManager.GPS_PROVIDER, LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER)
        } else {
            listOf(LocationManager.FUSED_PROVIDER, LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER)
        }
        val ordered = preferred.filter { providers.contains(it) }
        if (ordered.isEmpty()) return null

        val latch = CountDownLatch(1)
        val gotLocation = arrayOfNulls<Location>(1)
        val listeners = mutableListOf<Pair<String, LocationListener>>()
        // Must run requestLocationUpdates on a Looper thread. The Node-bridge
        // executor isn't a Looper, so we use the main looper for the listener
        // callbacks - the callback itself does no UI work.
        val looper = Looper.getMainLooper()
        try {
            for (p in ordered) {
                val listener = object : LocationListener {
                    override fun onLocationChanged(location: Location) {
                        synchronized(gotLocation) {
                            if (gotLocation[0] == null) {
                                gotLocation[0] = location
                                latch.countDown()
                            }
                        }
                    }
                    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
                    override fun onProviderEnabled(provider: String) {}
                    override fun onProviderDisabled(provider: String) {}
                }
                try {
                    lm.requestLocationUpdates(p, 0L, 0f, listener, looper)
                    listeners += p to listener
                } catch (e: SecurityException) {
                    Log.w(tag, "requestLocationUpdates SecurityException $p: ${e.message}")
                } catch (e: Exception) {
                    Log.w(tag, "requestLocationUpdates failed $p: ${e.message}")
                }
            }
            latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        } finally {
            for ((_, listener) in listeners) {
                try { lm.removeUpdates(listener) } catch (_: Exception) {}
            }
        }
        return gotLocation[0]
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
            // Sleep ~700 ms before returning ok so the app's first frame
            // has time to paint and AccessibilityService populates the
            // node tree. Without this delay, the model's immediately-next
            // ui.read_screen call hits the previous app (or a blank
            // launching screen) and decides "I can't see anything to tap".
            // 700 ms is enough for warm-launches of WhatsApp / Swiggy
            // / Settings on a mid-range Samsung; cold-launches still need
            // a second ui.read_screen and that's fine.
            try { Thread.sleep(700) } catch (_: InterruptedException) {}
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("launched", true)
                .put("package", resolved)
                .put("settle_ms", 700))
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

    // ---------- ALARM ----------
    /**
     * Schedule an alarm via android.provider.AlarmClock.ACTION_SET_ALARM. No
     * runtime permission required - the system handles it. Most devices show
     * the alarm UI for confirmation; some (Samsung One UI 6+) skip the UI
     * when EXTRA_SKIP_UI is set, which we do for hands-free voice flows.
     */
    fun setAlarm(ctx: Context, args: JSONObject): JSONObject {
        val hour = args.optInt("hour", -1)
        val minute = args.optInt("minute", 0)
        val label = args.optString("label", "Ben alarm")
        if (hour !in 0..23 || minute !in 0..59) {
            return errorResult("invalid_time", "Pass hour 0..23 and minute 0..59 (24-hour clock).")
        }
        val intent = Intent(android.provider.AlarmClock.ACTION_SET_ALARM).apply {
            putExtra(android.provider.AlarmClock.EXTRA_HOUR, hour)
            putExtra(android.provider.AlarmClock.EXTRA_MINUTES, minute)
            putExtra(android.provider.AlarmClock.EXTRA_MESSAGE, label)
            putExtra(android.provider.AlarmClock.EXTRA_SKIP_UI, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            ctx.startActivity(intent)
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("scheduled", true)
                .put("hour", hour)
                .put("minute", minute)
                .put("label", label))
        } catch (e: Exception) {
            errorResult("alarm_failed", e.message ?: "No clock app on this device that handles ACTION_SET_ALARM.")
        }
    }

    // ---------- TIMER ----------
    fun setTimer(ctx: Context, args: JSONObject): JSONObject {
        val seconds = args.optInt("seconds", -1)
        val label = args.optString("label", "Ben timer")
        if (seconds <= 0 || seconds > 24 * 3600) {
            return errorResult("invalid_seconds", "Pass seconds in (0, 86400].")
        }
        val intent = Intent(android.provider.AlarmClock.ACTION_SET_TIMER).apply {
            putExtra(android.provider.AlarmClock.EXTRA_LENGTH, seconds)
            putExtra(android.provider.AlarmClock.EXTRA_MESSAGE, label)
            putExtra(android.provider.AlarmClock.EXTRA_SKIP_UI, true)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            ctx.startActivity(intent)
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("started", true)
                .put("seconds", seconds)
                .put("label", label))
        } catch (e: Exception) {
            errorResult("timer_failed", e.message ?: "No clock app on this device that handles ACTION_SET_TIMER.")
        }
    }

    // ---------- CALENDAR ----------
    /**
     * Open the calendar app's "new event" form prefilled with title / time /
     * location. We deliberately use the Insert intent (not direct write to
     * CalendarContract) so we don't need the WRITE_CALENDAR permission and
     * the user gets a confirmation step.
     */
    fun addCalendarEvent(ctx: Context, args: JSONObject): JSONObject {
        val title = args.optString("title", "").trim()
        if (title.isEmpty()) return errorResult("title_required")
        val description = args.optString("description", "")
        val location = args.optString("location", "")
        val startMs = parseTimeArg(args.optString("start", "")) ?: System.currentTimeMillis() + 60 * 60_000
        val endMs = parseTimeArg(args.optString("end", ""))
            ?: (startMs + (args.optLong("duration_minutes", 30) * 60_000))
        val intent = Intent(Intent.ACTION_INSERT).apply {
            data = android.provider.CalendarContract.Events.CONTENT_URI
            putExtra(android.provider.CalendarContract.Events.TITLE, title)
            putExtra(android.provider.CalendarContract.Events.DESCRIPTION, description)
            putExtra(android.provider.CalendarContract.Events.EVENT_LOCATION, location)
            putExtra(android.provider.CalendarContract.EXTRA_EVENT_BEGIN_TIME, startMs)
            putExtra(android.provider.CalendarContract.EXTRA_EVENT_END_TIME, endMs)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            ctx.startActivity(intent)
            JSONObject().put("ok", true).put("result", JSONObject()
                .put("opened", true)
                .put("title", title)
                .put("start_ms", startMs)
                .put("end_ms", endMs)
                .put("note", "Calendar UI opened with prefilled fields. User taps Save."))
        } catch (e: Exception) {
            errorResult("calendar_failed", e.message ?: "")
        }
    }

    /** Accept ISO-8601 (2026-05-08T07:30:00) or HH:mm-of-today as start time. */
    private fun parseTimeArg(s: String): Long? {
        if (s.isBlank()) return null
        // ISO with optional Z / offset
        try {
            val instant = if (s.contains('T')) {
                if (s.endsWith("Z") || Regex("[+\\-]\\d\\d:?\\d\\d$").containsMatchIn(s)) {
                    java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
                } else {
                    java.time.LocalDateTime.parse(s).atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
                }
            } else if (s.matches(Regex("^\\d{1,2}:\\d{2}$"))) {
                val parts = s.split(":")
                val now = java.util.Calendar.getInstance().apply {
                    set(java.util.Calendar.HOUR_OF_DAY, parts[0].toInt())
                    set(java.util.Calendar.MINUTE, parts[1].toInt())
                    set(java.util.Calendar.SECOND, 0)
                    set(java.util.Calendar.MILLISECOND, 0)
                }
                if (now.timeInMillis < System.currentTimeMillis()) {
                    now.add(java.util.Calendar.DAY_OF_MONTH, 1)
                }
                now.timeInMillis
            } else {
                return s.toLongOrNull()
            }
            return instant
        } catch (_: Exception) {
            return null
        }
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
