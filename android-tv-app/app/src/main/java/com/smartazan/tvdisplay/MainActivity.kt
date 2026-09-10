package com.smartazan.tvdisplay

import android.app.Activity
import android.app.AlertDialog
import android.content.SharedPreferences
import android.net.http.SslError
import android.os.Bundle
import android.text.InputType
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
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
 */
class MainActivity : Activity() {

    private lateinit var webView: WebView
    private lateinit var prefs: SharedPreferences
    private var setupDialog: AlertDialog? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        hideSystemUi()

        prefs = getSharedPreferences("smart_azan_tv", MODE_PRIVATE)

        webView = WebView(this)
        setContentView(webView)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        // Autoplay the azan <audio> element without needing a tap first -
        // this is the equivalent of Fully Kiosk's "Autoplay of Videos/Audio".
        webView.settings.mediaPlaybackRequiresUserGesture = false
        webView.settings.loadWithOverviewMode = true
        webView.settings.useWideViewPort = true

        webView.webChromeClient = WebChromeClient()
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
    }

    private fun loadConfiguredUrl() {
        val base = prefs.getString("server_url", null) ?: return
        webView.loadUrl(base.trimEnd('/') + "/tv-display")
    }

    private fun promptForUrl() {
        val input = EditText(this)
        input.hint = "https://192.168.1.42:5050"
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
            .setTitle("Smart Azan server address")
            .setMessage(
                "Enter the address shown when you open Smart Azan in a browser, " +
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
