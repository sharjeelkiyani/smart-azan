package com.smartazan.tvdisplay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.graphics.PixelFormat
import android.net.http.SslError
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Background watcher: polls the server for a new azan/dua even while some
 * other app (Netflix, the Fire TV home screen, whatever) is on screen, and
 * draws the display full-screen on top of it - this is what lets Smart
 * Azan actually take over the TV, not just react while it's already the
 * visible app (that part is handled by MainActivity's own WebView/JS).
 *
 * Requires the "draw over other apps" permission, which Android deliberately
 * makes the user grant by hand (Settings.canDrawOverlays) - no app, ours or
 * anyone else's (including Fully Kiosk), can silently take over the screen
 * without it. If it's not granted, this service just stays idle.
 */
class AzanOverlayService : Service() {

    companion object {
        const val CHANNEL_ID = "azan_watcher"
        const val NOTIFICATION_ID = 1
        const val POLL_INTERVAL_MS = 5000L
        const val OVERLAY_AUTO_DISMISS_MS = 6 * 60 * 1000L
    }

    private lateinit var prefs: SharedPreferences
    private val mainHandler = Handler(Looper.getMainLooper())
    private val pollHandler = Handler(Looper.getMainLooper())
    private var lastPlayId = -1
    private var overlayView: WebView? = null
    private var windowManager: WindowManager? = null
    private var running = false

    private val pollRunnable = object : Runnable {
        override fun run() {
            Thread { pollOnce() }.start()
            if (running) pollHandler.postDelayed(this, POLL_INTERVAL_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences("smart_azan_tv", MODE_PRIVATE)
        startForeground(NOTIFICATION_ID, buildNotification())
        running = true
        pollHandler.post(pollRunnable)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    private fun buildNotification(): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(
                CHANNEL_ID, "Smart Azan watcher", NotificationManager.IMPORTANCE_MIN
            )
            mgr.createNotificationChannel(channel)
        }
        val launchIntent = Intent(this, MainActivity::class.java)
        val pending = PendingIntent.getActivity(
            this, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Smart Azan")
            .setContentText("Watching for the next azan")
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentIntent(pending)
            .setOngoing(true)
            .build()
    }

    private fun pollOnce() {
        val base = (prefs.getString("server_url", null) ?: return).trimEnd('/')
        try {
            val conn = URL("$base/tv_status").openConnection() as HttpURLConnection
            conn.connectTimeout = 4000
            conn.readTimeout = 4000
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            conn.disconnect()

            val json = JSONObject(text)
            val playId = json.optInt("play_id", -1)
            val filename = json.optString("filename", "")

            if (filename.isNotEmpty() && playId != lastPlayId) {
                lastPlayId = playId
                // Skip if our own app is already the visible one - its own
                // WebView/JS is already handling this exact event, and
                // showing the overlay on top of ourselves would just
                // double the audio.
                if (!MainActivity.isForeground) {
                    mainHandler.post { showOverlay(base, filename) }
                }
            }
        } catch (e: Exception) {
            // Server unreachable/slow - just retry on the next tick.
        }
    }

    private fun showOverlay(base: String, filename: String) {
        if (!Settings.canDrawOverlays(this)) return
        if (overlayView != null) return

        wakeScreen()

        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val webView = WebView(this)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedSslError(view: WebView?, handler: SslErrorHandler?, error: SslError?) {
                handler?.proceed()
            }
        }

        val overlayType = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        else
            @Suppress("DEPRECATION") WindowManager.LayoutParams.TYPE_SYSTEM_ALERT

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.MATCH_PARENT,
            overlayType,
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START

        try {
            webView.loadUrl("$base/tv-display?play=" + URLEncoder.encode(filename, "UTF-8"))
            windowManager?.addView(webView, params)
            overlayView = webView
            mainHandler.postDelayed({ removeOverlay() }, OVERLAY_AUTO_DISMISS_MS)
        } catch (e: Exception) {
            overlayView = null
        }
    }

    private fun wakeScreen() {
        try {
            val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
            @Suppress("DEPRECATION")
            val wl = pm.newWakeLock(
                PowerManager.SCREEN_BRIGHT_WAKE_LOCK or PowerManager.ACQUIRE_CAUSES_WAKEUP,
                "SmartAzanTV:wake"
            )
            wl.acquire(10000)
        } catch (e: Exception) {
            // Not fatal - the overlay will still show, just might not wake a sleeping screen.
        }
    }

    private fun removeOverlay() {
        overlayView?.let {
            try { windowManager?.removeView(it) } catch (e: Exception) {}
        }
        overlayView = null
    }

    override fun onDestroy() {
        super.onDestroy()
        running = false
        pollHandler.removeCallbacks(pollRunnable)
        removeOverlay()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
