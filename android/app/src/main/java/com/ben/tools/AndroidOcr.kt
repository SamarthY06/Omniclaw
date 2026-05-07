package com.ben.tools

import android.content.Context
import android.graphics.BitmapFactory
import android.net.Uri
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Wraps Google ML Kit on-device text recognition.
 *
 * Output shape MUST match omniclaw/tools/macos_ocr.py so android_vision.ts and
 * the test_android_ocr_parity.py harness can compare results 1:1:
 *
 * { ok: true, image_width, image_height,
 *   items: [ {text, confidence, bbox: {x, y, w, h}} ... ] }
 */
object AndroidOcr {
    fun recognize(ctx: Context, imagePath: String): JSONObject {
        val file = File(imagePath)
        if (!file.exists()) {
            return JSONObject().put("ok", false).put("error", "image_not_found")
        }
        val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imagePath, opts)
        val width = opts.outWidth
        val height = opts.outHeight
        if (width <= 0 || height <= 0) {
            return JSONObject().put("ok", false).put("error", "decode_failed")
        }
        val image = InputImage.fromFilePath(ctx, Uri.fromFile(file))
        val recognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        val items = JSONArray()
        var error: String? = null
        val latch = CountDownLatch(1)
        recognizer.process(image)
            .addOnSuccessListener { result ->
                for (block in result.textBlocks) {
                    for (line in block.lines) {
                        val rect = line.boundingBox ?: continue
                        items.put(
                            JSONObject()
                                .put("text", line.text ?: "")
                                .put("confidence", line.confidence)
                                .put(
                                    "bbox",
                                    JSONObject()
                                        .put("x", rect.left)
                                        .put("y", rect.top)
                                        .put("w", rect.width())
                                        .put("h", rect.height()),
                                ),
                        )
                    }
                }
                latch.countDown()
            }
            .addOnFailureListener { e ->
                error = e.message ?: e.javaClass.simpleName
                latch.countDown()
            }
        if (!latch.await(8, TimeUnit.SECONDS)) error = "timeout"
        return JSONObject()
            .put("ok", error == null)
            .put("error", error ?: JSONObject.NULL)
            .put("image_width", width)
            .put("image_height", height)
            .put("items", items)
    }
}
