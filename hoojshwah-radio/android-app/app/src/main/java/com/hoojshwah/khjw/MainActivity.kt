package com.hoojshwah.khjw

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.OptIn
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.net.toUri
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionResult
import androidx.media3.session.SessionToken
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.google.common.util.concurrent.ListenableFuture
import com.hoojshwah.khjw.playback.KhjwPlaybackService
import com.hoojshwah.khjw.playback.NativePlaybackCommands

@OptIn(UnstableApi::class)
class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView

    private var controllerFuture: ListenableFuture<MediaController>? = null
    private var controller: MediaController? = null

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.station_webview)

        configureSystemBarInsets()
        configureWebView()
        requestNotificationPermissionIfNeeded()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })
    }

    override fun onStart() {
        super.onStart()
        connectController()
    }

    override fun onStop() {
        controller?.removeListener(controllerListener)
        controller = null
        controllerFuture?.let { MediaController.releaseFuture(it) }
        controllerFuture = null
        super.onStop()
    }

    override fun onDestroy() {
        webView.stopLoading()
        webView.webChromeClient = null
        webView.webViewClient = WebViewClient()
        webView.destroy()
        super.onDestroy()
    }

    private fun configureSystemBarInsets() {
        val root = findViewById<android.view.View>(R.id.activity_root)

        ViewCompat.setOnApplyWindowInsetsListener(root) { view, windowInsets ->
            val safeInsets = windowInsets.getInsets(
                WindowInsetsCompat.Type.systemBars() or
                    WindowInsetsCompat.Type.displayCutout() or
                    WindowInsetsCompat.Type.ime()
            )
            view.setPadding(safeInsets.left, safeInsets.top, safeInsets.right, safeInsets.bottom)
            windowInsets
        }
        ViewCompat.requestApplyInsets(root)
    }

    private fun connectController() {
        val token = SessionToken(this, ComponentName(this, KhjwPlaybackService::class.java))
        val future = MediaController.Builder(this, token).buildAsync()
        controllerFuture = future

        future.addListener(
            {
                runCatching { future.get() }
                    .onSuccess { connectedController ->
                        if (controllerFuture !== future) {
                            connectedController.release()
                            return@onSuccess
                        }
                        controller = connectedController
                        connectedController.addListener(controllerListener)
                        renderControllerState()
                    }
                    .onFailure { postNativePlaybackState(false) }
            },
            ContextCompat.getMainExecutor(this),
        )
    }

    private val controllerListener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) {
            renderControllerState()
        }
    }

    private fun renderControllerState() {
        postNativePlaybackState(controller?.isPlaying == true)
    }

    private fun configureWebView() {
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = true
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            allowFileAccess = false
            allowContentAccess = false
            javaScriptCanOpenWindowsAutomatically = false
            setGeolocationEnabled(false)
            setSupportMultipleWindows(false)
            safeBrowsingEnabled = true
        }

        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(
                webView,
                NATIVE_PLAYER_BRIDGE,
                setOf(KHJW_ORIGIN),
            ) { _, message, sourceOrigin, isMainFrame, replyProxy ->
                if (!isMainFrame || !isProductionOrigin(sourceOrigin)) return@addWebMessageListener

                val messageData = message.data ?: return@addWebMessageListener

                when (messageData) {
                    "play" -> controller?.play()
                    "pause" -> controller?.pause()
                    else -> {
                        val trackId = NativePlaybackCommands.parseBackstageTrackId(messageData)
                            ?: return@addWebMessageListener
                        val connectedController = controller
                        if (connectedController == null) {
                            replyProxy.postMessage(BACKSTAGE_UNAVAILABLE_STATE)
                            return@addWebMessageListener
                        }

                        val result = connectedController.sendCustomCommand(
                            NativePlaybackCommands.selectBackstageTrack,
                            Bundle().apply {
                                putString(NativePlaybackCommands.ARG_TRACK_ID, trackId)
                            },
                        )
                        result.addListener(
                            {
                                val state = runCatching { result.get() }
                                    .map { commandResult ->
                                        if (commandResult.resultCode == SessionResult.RESULT_SUCCESS) {
                                            BACKSTAGE_ACCEPTED_STATE
                                        } else {
                                            BACKSTAGE_UNAVAILABLE_STATE
                                        }
                                    }
                                    .getOrDefault(BACKSTAGE_UNAVAILABLE_STATE)
                                replyProxy.postMessage(state)
                            },
                            ContextCompat.getMainExecutor(this),
                        )
                        return@addWebMessageListener
                    }
                }

                replyProxy.postMessage(nativePlaybackState())
            }
        }

        webView.webChromeClient = WebChromeClient()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val uri = request.url
                if (uri.scheme == "https" && uri.host == KHJW_HOST) {
                    if (uri.getQueryParameter("app") == "android") return false

                    val androidUri = uri.buildUpon().clearQuery().apply {
                        uri.queryParameterNames
                            .filterNot { it == "app" }
                            .forEach { name ->
                                uri.getQueryParameters(name).forEach { value ->
                                    appendQueryParameter(name, value)
                                }
                            }
                        appendQueryParameter("app", "android")
                    }.build()
                    view.loadUrl(androidUri.toString())
                    return true
                }

                if (uri.scheme == "http" || uri.scheme == "https") {
                    runCatching { startActivity(Intent(Intent.ACTION_VIEW, uri)) }
                }
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                super.onPageFinished(view, url)
                postNativePlaybackState(controller?.isPlaying == true)
            }
        }

        webView.loadUrl(KHJW_ANDROID_URL)
    }

    private fun postNativePlaybackState(isPlaying: Boolean = controller?.isPlaying == true) {
        if (!::webView.isInitialized || !isProductionOrigin((webView.url ?: return).toUri())) return
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.POST_WEB_MESSAGE)) return

        WebViewCompat.postWebMessage(
            webView,
            WebMessageCompat(if (isPlaying) PLAYING_STATE else PAUSED_STATE),
            KHJW_ORIGIN.toUri(),
        )
    }

    private fun nativePlaybackState(): String =
        if (controller?.isPlaying == true) PLAYING_STATE else PAUSED_STATE

    private fun isProductionOrigin(uri: Uri): Boolean =
        uri.scheme == "https" &&
            uri.host == KHJW_HOST &&
            (uri.port == -1 || uri.port == 443)

    private fun requestNotificationPermissionIfNeeded() {
        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    companion object {
        private const val KHJW_HOST = "hoojshwah-radio-live.onrender.com"
        private const val KHJW_ORIGIN = "https://$KHJW_HOST"
        private const val KHJW_ANDROID_URL =
            "$KHJW_ORIGIN/?app=android"
        private const val NATIVE_PLAYER_BRIDGE = "khjwNativePlayer"
        private const val PLAYING_STATE = "playing"
        private const val PAUSED_STATE = "paused"
        private const val BACKSTAGE_ACCEPTED_STATE = "backstage-accepted"
        private const val BACKSTAGE_UNAVAILABLE_STATE = "backstage-unavailable"
    }
}
