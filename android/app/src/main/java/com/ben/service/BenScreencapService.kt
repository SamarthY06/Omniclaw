package com.ben.service

import android.app.Activity
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.IBinder
import android.util.DisplayMetrics
import android.util.Log
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import com.ben.MainActivity
import com.ben.R
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * MediaProjection screencap. Caller pattern:
 *
 *   1. Activity calls BenScreencapService.requestProjection(activity, requestCode)
 *      to launch the MediaProjection consent dialog (must be done from an Activity).
 *   2. Activity forwards the resultCode + data to BenScreencapService.bind(...).
 *   3. After that, screenshotBlocking() can be called repeatedly without re-prompting.
 *
 * Pretty stateful, so we cache the projection in a static AtomicReference. We
 * don't try to share across processes - the Node bridge runs in the same process.
 */
class BenScreencapService : Service() {
    private val tag = "BenScreencapService"

    override fun onCreate() {
        super.onCreate()
        startForegroundOk()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_BIND) {
            val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
            val data: Intent? = intent.getParcelableExtra(EXTRA_RESULT_DATA)
            if (data != null && resultCode == Activity.RESULT_OK) {
                val mgr = getSystemService(MediaProjectionManager::class.java)
                projectionRef.set(mgr.getMediaProjection(resultCode, data))
                Log.i(tag, "MediaProjection bound")
            }
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun startForegroundOk() {
        val pi = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notif = NotificationCompat.Builder(this, getString(R.string.notif_channel_id))
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(getString(R.string.app_name))
            .setContentText("Screencap active")
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
        startForeground(4712, notif)
    }

    companion object {
        const val ACTION_BIND = "com.ben.screencap.BIND"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"

        private val projectionRef = AtomicReference<MediaProjection?>(null)

        /** Activity helper - launches the consent dialog. */
        fun makeProjectionRequestIntent(ctx: Context): Intent {
            val mgr = ctx.getSystemService(MediaProjectionManager::class.java)
            return mgr.createScreenCaptureIntent()
        }

        fun bind(ctx: Context, resultCode: Int, data: Intent) {
            val intent = Intent(ctx, BenScreencapService::class.java)
                .setAction(ACTION_BIND)
                .putExtra(EXTRA_RESULT_CODE, resultCode)
                .putExtra(EXTRA_RESULT_DATA, data)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(intent) else ctx.startService(intent)
        }

        /**
         * Synchronously capture one frame to PNG. Returns
         * { ok, path, width, height, error? }
         */
        fun screenshotBlocking(ctx: Context, optionalPath: String?, app: String?): JSONObject {
            val projection = projectionRef.get()
                ?: return JSONObject().put("ok", false).put("error", "media_projection_not_bound")

            // Optional: focus an app first (re-uses Accessibility helper).
            if (app != null) {
                BenAccessibilityService.live?.launchApp(JSONObject().put("package", app))
                try { Thread.sleep(250) } catch (_: InterruptedException) {}
            }

            val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
            val metrics = DisplayMetrics()
            @Suppress("DEPRECATION") wm.defaultDisplay.getRealMetrics(metrics)
            val width = metrics.widthPixels
            val height = metrics.heightPixels
            val density = metrics.densityDpi

            val reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
            val displayMgr = ctx.getSystemService(Context.DISPLAY_SERVICE) as DisplayManager
            // Reflective check that the projection is still alive
            val virtualDisplay: VirtualDisplay = projection.createVirtualDisplay(
                "ben-screencap",
                width, height, density,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.surface, null, null,
            )
            val latch = CountDownLatch(1)
            var image: Image? = null
            reader.setOnImageAvailableListener({ r ->
                image = r.acquireLatestImage()
                latch.countDown()
            }, null)

            val gotFrame = latch.await(3, TimeUnit.SECONDS)
            try {
                if (!gotFrame || image == null) {
                    return JSONObject().put("ok", false).put("error", "no_frame")
                }
                val outPath = optionalPath ?: File(ctx.cacheDir, "ben_screencap_${System.currentTimeMillis()}.png").absolutePath
                writePng(image!!, outPath, width, height)
                return JSONObject()
                    .put("ok", true).put("path", outPath)
                    .put("width", width).put("height", height)
            } finally {
                image?.close()
                virtualDisplay.release()
                reader.close()
            }
        }

        private fun writePng(image: Image, outPath: String, width: Int, height: Int) {
            val plane = image.planes[0]
            val buf = plane.buffer
            val pixelStride = plane.pixelStride
            val rowStride = plane.rowStride
            val rowPadding = rowStride - pixelStride * width
            val bitmap = Bitmap.createBitmap(
                width + rowPadding / pixelStride, height, Bitmap.Config.ARGB_8888,
            )
            bitmap.copyPixelsFromBuffer(buf)
            val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)
            FileOutputStream(outPath).use { fos ->
                cropped.compress(Bitmap.CompressFormat.PNG, 100, fos)
            }
            cropped.recycle()
            bitmap.recycle()
        }
    }
}
