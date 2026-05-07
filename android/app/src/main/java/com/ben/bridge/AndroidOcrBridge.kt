package com.ben.bridge

import android.content.Context
import com.ben.tools.AndroidOcr
import org.json.JSONObject

object AndroidOcrBridge {
    /**
     * Args: { "image_path": "/path/to/png" }
     * Returns: { ok, image_width, image_height, items[{text, confidence, bbox{x,y,w,h}}] }
     * Output shape is intentionally identical to omniclaw/tools/macos_ocr.py.
     */
    fun recognize(ctx: Context, args: JSONObject): JSONObject {
        val path = args.optString("image_path")
        if (path.isNullOrBlank()) {
            return JSONObject().put("ok", false).put("error", "missing image_path")
        }
        return AndroidOcr.recognize(ctx, path)
    }
}
