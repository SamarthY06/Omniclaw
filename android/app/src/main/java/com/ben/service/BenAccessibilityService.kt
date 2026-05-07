package com.ben.service

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Path
import android.graphics.Point
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.Display
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicReference

/**
 * Accessibility-driven UI automation. Mirrors omniclaw/tools/macos_ax.py:
 *
 *   tree              -> JSONObject{ root: {...}, count }
 *   click_by_ax_id    -> click on a previously-cached node (id from tree())
 *   click_at          -> dispatch a tap gesture at (x, y), optionally focusing app first
 *   type_text         -> input string into the focused text node (or the node specified)
 *   swipe / scroll    -> gesture
 *   focus(package)    -> bring an app to foreground (launch if needed)
 *   launch_app        -> like focus, but resets the activity stack
 *   screen_size       -> { width, height }
 *
 * Node ids handed out by tree() are stable for the duration of a single tree
 * dump (we keep them alive via [nodeCache]) so the LLM can chain "tree -> click".
 */
class BenAccessibilityService : AccessibilityService() {

    private val nodeCache = HashMap<String, AccessibilityNodeInfo>()
    private var nodeCacheGeneration = 0

    override fun onServiceConnected() {
        super.onServiceConnected()
        liveRef.set(this)
        Log.i(TAG, "BenAccessibilityService connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // No-op: we don't react to events; we're poll-driven.
    }

    override fun onInterrupt() {
        // Required override; nothing to do.
    }

    override fun onUnbind(intent: Intent?): Boolean {
        liveRef.compareAndSet(this, null)
        return super.onUnbind(intent)
    }

    // ---- public API used by AndroidAxBridge --------------------------------

    fun tree(@Suppress("UNUSED_PARAMETER") args: JSONObject): JSONObject {
        nodeCacheGeneration++
        nodeCache.clear()
        val activeWindow = rootInActiveWindow
            ?: return JSONObject().put("ok", false).put("error", "no_active_window")
        val rootJson = JSONObject()
        var count = 0
        fun walk(node: AccessibilityNodeInfo?, parentPath: String, depth: Int): JSONObject? {
            if (node == null) return null
            val nodeId = "$nodeCacheGeneration:$count"
            nodeCache[nodeId] = AccessibilityNodeInfo.obtain(node)
            count++
            val rect = Rect().also { node.getBoundsInScreen(it) }
            val obj = JSONObject().apply {
                put("ax_id", nodeId)
                put("class", node.className?.toString() ?: "")
                put("text", node.text?.toString() ?: "")
                put("content_description", node.contentDescription?.toString() ?: "")
                put("view_id", node.viewIdResourceName ?: "")
                put("clickable", node.isClickable)
                put("focusable", node.isFocusable)
                put("scrollable", node.isScrollable)
                put("editable", node.isEditable)
                put("enabled", node.isEnabled)
                put("checked", node.isChecked)
                put("selected", node.isSelected)
                put("password", node.isPassword)
                put("bounds", JSONObject()
                    .put("x", rect.left)
                    .put("y", rect.top)
                    .put("w", rect.width())
                    .put("h", rect.height()))
                put("depth", depth)
            }
            val children = JSONArray()
            for (i in 0 until node.childCount) {
                walk(node.getChild(i), "$parentPath/$i", depth + 1)?.let { children.put(it) }
            }
            obj.put("children", children)
            return obj
        }
        val root = walk(activeWindow, "", 0)
        if (root != null) rootJson.put("root", root)
        rootJson.put("ok", true)
        rootJson.put("count", count)
        rootJson.put("generation", nodeCacheGeneration)
        return rootJson
    }

    fun clickByAxId(args: JSONObject): JSONObject {
        val id = args.optString("ax_id")
        val node = nodeCache[id]
            ?: return JSONObject().put("ok", false).put("error", "stale_or_unknown_ax_id")
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        return JSONObject().put("ok", ok)
    }

    fun clickAt(args: JSONObject): JSONObject {
        val x = args.optInt("x", -1)
        val y = args.optInt("y", -1)
        val app = if (args.has("app")) args.optString("app") else null
        if (x < 0 || y < 0) return JSONObject().put("ok", false).put("error", "missing_xy")
        if (app != null) {
            // Best-effort foreground; ignore errors so click still tries.
            launchApp(JSONObject().put("package", app))
            try { Thread.sleep(250) } catch (_: InterruptedException) {}
        }
        val path = Path().apply { moveTo(x.toFloat(), y.toFloat()) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, 60))
            .build()
        var ok = false
        val latch = java.util.concurrent.CountDownLatch(1)
        dispatchGesture(gesture, object : GestureResultCallback() {
            override fun onCompleted(g: GestureDescription?) {
                ok = true
                latch.countDown()
            }
            override fun onCancelled(g: GestureDescription?) {
                latch.countDown()
            }
        }, null)
        latch.await(2, java.util.concurrent.TimeUnit.SECONDS)
        return JSONObject().put("ok", ok).put("x", x).put("y", y)
    }

    fun typeText(args: JSONObject): JSONObject {
        val text = args.optString("text")
        if (text.isNullOrBlank()) return JSONObject().put("ok", false).put("error", "missing_text")
        val target = args.optString("ax_id").let { id -> if (id.isNullOrBlank()) null else nodeCache[id] }
            ?: findFocusedEditable()
            ?: return JSONObject().put("ok", false).put("error", "no_editable_focus")
        val args2 = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        val ok = target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args2)
        return JSONObject().put("ok", ok)
    }

    fun swipe(args: JSONObject): JSONObject = doStroke(args, durationMs = 200)
    fun scroll(args: JSONObject): JSONObject = doStroke(args, durationMs = 350)

    fun focus(args: JSONObject): JSONObject = launchApp(args)

    fun launchApp(args: JSONObject): JSONObject {
        val pkg = args.optString("package")
        if (pkg.isNullOrBlank()) return JSONObject().put("ok", false).put("error", "missing_package")
        val pm: PackageManager = applicationContext.packageManager
        val intent = pm.getLaunchIntentForPackage(pkg)
            ?: return JSONObject().put("ok", false).put("error", "no_launch_intent")
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        applicationContext.startActivity(intent)
        return JSONObject().put("ok", true).put("package", pkg)
    }

    private fun doStroke(args: JSONObject, durationMs: Long): JSONObject {
        val x1 = args.optInt("x1", -1); val y1 = args.optInt("y1", -1)
        val x2 = args.optInt("x2", -1); val y2 = args.optInt("y2", -1)
        if (listOf(x1, y1, x2, y2).any { it < 0 }) return JSONObject().put("ok", false).put("error", "missing_xy")
        val path = Path().apply { moveTo(x1.toFloat(), y1.toFloat()); lineTo(x2.toFloat(), y2.toFloat()) }
        val g = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, durationMs))
            .build()
        var ok = false
        val latch = java.util.concurrent.CountDownLatch(1)
        dispatchGesture(g, object : GestureResultCallback() {
            override fun onCompleted(d: GestureDescription?) { ok = true; latch.countDown() }
            override fun onCancelled(d: GestureDescription?) { latch.countDown() }
        }, null)
        latch.await(2, java.util.concurrent.TimeUnit.SECONDS)
        return JSONObject().put("ok", ok)
    }

    private fun findFocusedEditable(): AccessibilityNodeInfo? {
        val root = rootInActiveWindow ?: return null
        val focus = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (focus != null && focus.isEditable) return focus
        // Fallback: BFS for the first editable node.
        val q = ArrayDeque<AccessibilityNodeInfo>()
        q.add(root)
        while (q.isNotEmpty()) {
            val n = q.removeFirst()
            if (n.isEditable && n.isVisibleToUser) return n
            for (i in 0 until n.childCount) q.add(n.getChild(i) ?: continue)
        }
        return null
    }

    companion object {
        private const val TAG = "BenAccessibilityService"
        private val liveRef = AtomicReference<BenAccessibilityService?>(null)
        val live: BenAccessibilityService? get() = liveRef.get()

        /** Static screen-size that works without the Accessibility service running. */
        fun screenSizeStatic(ctx: Context): JSONObject {
            val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val (w, h) = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                val bounds = wm.currentWindowMetrics.bounds
                bounds.width() to bounds.height()
            } else {
                @Suppress("DEPRECATION")
                val display: Display = wm.defaultDisplay
                @Suppress("DEPRECATION")
                val size = Point().also { display.getRealSize(it) }
                size.x to size.y
            }
            return JSONObject().put("ok", true).put("width", w).put("height", h)
        }
    }
}
