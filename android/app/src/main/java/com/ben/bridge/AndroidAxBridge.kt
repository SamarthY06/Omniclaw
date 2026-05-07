package com.ben.bridge

import android.content.Context
import com.ben.service.BenAccessibilityService
import org.json.JSONObject

/**
 * Static facade routing JSON-RPC calls (named "ax.*") into the running
 * BenAccessibilityService. Returning a JSONObject (rather than throwing) lets
 * us keep RPC error semantics consistent across all bridge methods.
 */
object AndroidAxBridge {
    fun tree(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.tree(args) }
    fun clickByAxId(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.clickByAxId(args) }
    fun clickAt(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.clickAt(args) }
    fun type(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.typeText(args) }
    fun swipe(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.swipe(args) }
    fun scroll(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.scroll(args) }
    fun focus(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.focus(args) }
    fun launchApp(ctx: Context, args: JSONObject): JSONObject = svc(ctx) { it.launchApp(args) }
    fun screenSize(ctx: Context, args: JSONObject): JSONObject {
        // screen_size doesn't actually need the AccessibilityService - the WindowManager works
        // even without it. So we route through a static helper.
        return BenAccessibilityService.screenSizeStatic(ctx)
    }

    private inline fun svc(ctx: Context, block: (BenAccessibilityService) -> JSONObject): JSONObject {
        val live = BenAccessibilityService.live
            ?: return JSONObject().put("ok", false).put("error", "accessibility_service_not_running")
        return block(live)
    }
}
