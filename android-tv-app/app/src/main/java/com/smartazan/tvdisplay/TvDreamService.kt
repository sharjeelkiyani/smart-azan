package com.smartazan.tvdisplay

import android.net.http.SslError
import android.service.dreams.DreamService
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient

/**
 * Fire TV/Android TV screensaver - shows the same TV display during idle
 * time. Chosen manually by the user in Settings -> Display & Sounds ->
 * Screen Saver, like any other screensaver. This only ever activates when
 * the device is idle; it can't interrupt something actively playing (that's
 * what AzanOverlayService is for).
 */
class TvDreamService : DreamService() {

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        isInteractive = false
        isFullscreen = true

        val prefs = getSharedPreferences("smart_azan_tv", MODE_PRIVATE)
        val base = prefs.getString("server_url", null)

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
        setContentView(webView)

        if (!base.isNullOrBlank()) {
            webView.loadUrl(base.trimEnd('/') + "/tv-display")
        }
    }
}
