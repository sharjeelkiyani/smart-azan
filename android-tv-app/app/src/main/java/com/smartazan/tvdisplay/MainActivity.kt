package com.smartazan.tvdisplay

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.util.Log
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.webkit.ConsoleMessage
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText

/**
 * Full-screen WebView pointed at the Smart Azan server's /tv-display page.
 * That page polls the server itself for new azan/dua events (see
 * app.py's /tv_status), so this app only has to load it once and stay
 * there - no remote-control protocol needed, unlike Fully Kiosk's paid
 * Remote Admin API.
 *
 * Taking over the screen while some OTHER app is active (not just reacting
 * while this app is already what's showing) is handled separately by
 * AzanOverlayService, started below - that's a background watcher with its
 * own permission requirements, since Android deliberately doesn't let a
 * plain foreground app do that on its own.
 */
class MainActivity : Activity() {

    companion object {
        // Checked by AzanOverlayService so it doesn't draw its own overlay
        // (and double the audio) when this Activity is already the thing
        // on screen handling the same event via its own WebView/JS.
        @Volatile
        var isForeground = false
    }

    private lateinit var webView: WebView
    private lateinit var prefs: SharedPreferences
    private var setupDialog: AlertDialog? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUi()

        prefs = getSharedPreferences("smart_azan_tv", MODE_PRIVATE)
        startOverlayService()

        webView = WebView(this)
        setContentView(webView)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        // Autoplay the azan <audio> element without needing a tap first -
        // this is the equivalent of Fully Kiosk's "Autoplay of Videos/Audio".
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.settings.loadWithOverviewMode = true
        webView.settings.useWideViewPort = true

        // Forwards the page's console.log/error to logcat (tag "SmartAzanTV")
        // so JS-side failures (a rejected audio.play(), a fetch error) are
        // actually visible instead of silently swallowed - the page's own
        // try/catch blocks were logging to a console nobody could see.
        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(msg: ConsoleMessage?): Boolean {
                Log.d("SmartAzanTV", "console: ${msg?.message()}")
                return true
            }
        }
        webView.webViewClient = object : WebViewClient() {
            // The Smart Azan server uses a self-signed HTTPS certificate
            // (it's your own private LAN device, not a public site) -
            // accept it instead of showing a certificate error page. This
            // is the equivalent of Fully Kiosk's "Ignore SSL Errors".
            override fun onReceivedSslError(
                view: WebView?,
                handler: SslErrorHandler?,
                error: SslError?
            ) {
                handler?.proceed()
            }

            override fun onReceivedError(
                view: WebView?,
                errorCode: Int,
                description: String?,
                failingUrl: String?
            ) {
                // Covers launching at boot before the network/server is
                // actually reachable yet - just keep retrying.
                webView.postDelayed({ loadConfiguredUrl() }, 5000)
            }
        }

        val savedUrl = prefs.getString("server_url", null)
        if (savedUrl.isNullOrBlank()) {
            promptForUrl()
        } else {
            loadConfiguredUrl()
        }

        maybeRequestOverlayPermission()
    }

    override fun onResume() {
        super.onResume()
        isForeground = true
    }

    override fun onPause() {
        super.onPause()
        isForeground = false
    }

    private fun startOverlayService() {
        val intent = Intent(this, AzanOverlayService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }

    // "Draw over other apps" lets the background watcher (AzanOverlayService)
    // show azan on top of whatever else is running - Android requires this
    // to be granted by hand in Settings, no app can silently turn it on for
    // itself. Without it, azan still plays/displays fine whenever this app
    // is already the one on screen; it just can't take over from something
    // else automatically.
    private fun maybeRequestOverlayPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        if (Settings.canDrawOverlays(this)) return

        AlertDialog.Builder(this)
            .setTitle("Show azan over other apps")
            .setMessage(
                "To have Smart Azan automatically take over the screen at azan time - even while " +
                    "something else is playing - it needs the \"draw over other apps\" permission. " +
                    "Without it, azan still shows whenever this app is already open."
            )
            .setCancelable(true)
            .setPositiveButton("Open Settings") { _, _ ->
                val intent = Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                )
                startActivity(intent)
            }
            .setNegativeButton("Not now", null)
            .show()
    }

    private fun loadConfiguredUrl() {
        val base = prefs.getString("server_url", null) ?: return
        webView.loadUrl(base.trimEnd('/') + "/tv-display")
    }

    private fun promptForUrl() {
        val input = EditText(this)
        input.hint = "http://192.168.1.42:5051"
        input.setSingleLine(true)
        // A remote-control on-screen keyboard behaves much better with a
        // single-line, URL-flavoured field: it puts "/" and "." within
        // easy reach and shows a "Done" action key instead of a newline -
        // that "Done" key is what actually submits the dialog below,
        // since D-pad navigation from this field down to a separate Save
        // button doesn't reliably work on every remote/launcher.
        input.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        input.imeOptions = EditorInfo.IME_ACTION_DONE
        prefs.getString("server_url", null)?.let { input.setText(it) }

        fun trySave(): Boolean {
            val url = input.text.toString().trim()
            if (url.isEmpty()) return false
            prefs.edit().putString("server_url", url).apply()
            setupDialog?.dismiss()
            setupDialog = null
            loadConfiguredUrl()
            return true
        }
        input.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) trySave() else false
        }

        setupDialog = AlertDialog.Builder(this)
            .setTitle("Smart Azan TV display address")
            .setMessage(
                "Enter the TV display address shown on the Integrations page in Smart Azan " +
                    "(not the https:// address you use in a browser - this is a separate one), " +
                    "then press Done on the keyboard to save."
            )
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Save") { _, _ -> trySave() }
            .show()
    }

    // A non-cancelable dialog already blocks tapping outside it or a plain
    // Back press from closing it - but Activity.onBackPressed() runs
    // independently of that and would otherwise close the whole app out
    // from under the dialog. Swallow Back entirely while it's showing.
    override fun onBackPressed() {
        if (setupDialog?.isShowing == true) return
        super.onBackPressed()
    }

    // Hold Back to change the server address later - the same "hold a
    // button to reach settings" pattern as Fully Kiosk and most other
    // remote-control-only kiosk/display apps use.
    override fun onKeyLongPress(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && setupDialog?.isShowing != true) {
            promptForUrl()
            return true
        }
        return super.onKeyLongPress(keyCode, event)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUi()
    }

    private fun hideSystemUi() {
        // The newer WindowInsetsController API needs API 30+; these flags
        // cover the same result back to minSdk 21, which matters here since
        // older Fire TV Stick models are still in real use.
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            )
    }
}
