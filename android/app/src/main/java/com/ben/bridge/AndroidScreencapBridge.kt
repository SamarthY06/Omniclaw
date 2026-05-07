package com.ben.bridge

import android.content.Context
import com.ben.service.BenScreencapService
import org.json.JSONObject

object AndroidScreencapBridge {
    /**
     * Triggers a one-shot screencap. The Node side receives a path on the local
     * filesystem and reads the PNG itself.
     *
     * Args:
     *   { "path"?: "/path/to/output.png" }  // optional; default is cacheDir/ben_screencap_<ts>.png
     *   { "app"?: "com.whatsapp" }          // optional - focuses the package first
     */
    fun screenshot(ctx: Context, args: JSONObject): JSONObject {
        val path = if (args.has("path")) args.getString("path") else null
        val app = if (args.has("app")) args.getString("app") else null
        return BenScreencapService.screenshotBlocking(ctx, path, app)
    }
}
