package com.woodshedwoodchuck.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Insets;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.util.Log;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.ConsoleMessage;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SafeBrowsingResponse;
import android.webkit.SslErrorHandler;
import android.webkit.WebBackForwardList;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.window.OnBackInvokedCallback;
import android.window.OnBackInvokedDispatcher;

import java.io.ByteArrayInputStream;

public final class MainActivity extends Activity {
    private static final int AUDIO_PERMISSION = 41;
    private final NavigationPolicy urls = new NavigationPolicy(ShellConfig.ORIGIN);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private WebView web;
    private WebView popupRouter;
    private FrameLayout container;
    private View statusPanel;
    private View progress;
    private View retry;
    private TextView statusMessage;
    private String intendedUrl = ShellConfig.ORIGIN + "/";
    private int pageGeneration;
    private int backGeneration;
    private boolean resumed;
    private boolean failed;
    private boolean committed;
    private boolean backPending;
    private final MicrophonePermissionFlow<PermissionRequest> microphone = new MicrophonePermissionFlow<>(urls);
    private OnBackInvokedCallback backCallback;
    private final Runnable loadTimeout = () -> showFailure(R.string.connection_error);
    private final Runnable closePopup = this::destroyPopup;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.activity_main);
        container = findViewById(R.id.web_container);
        statusPanel = findViewById(R.id.status_panel);
        progress = findViewById(R.id.progress);
        retry = findViewById(R.id.retry);
        statusMessage = findViewById(R.id.status_message);
        configureInsets();
        retry.setOnClickListener(v -> loadTrusted(intendedUrl));
        if (Build.VERSION.SDK_INT >= 33) {
            backCallback = this::handleBack;
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, backCallback);
        }
        // Never consume incoming Intent URLs or restore an authenticated page snapshot.
        loadTrusted(ShellConfig.ORIGIN + "/");
    }

    @SuppressWarnings("deprecation")
    private void configureInsets() {
        View root = findViewById(R.id.root);
        if (Build.VERSION.SDK_INT >= 30) {
            // API 35/36 enforces an edge-to-edge window. Inset the native container once,
            // then consume those insets so WebView/CSS does not apply them a second time.
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                Insets safe = insets.getInsets(WindowInsets.Type.systemBars()
                        | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom);
                return WindowInsets.CONSUMED;
            });
            root.requestApplyInsets();
        } else {
            // Ordinary fitted decor and adjustResize on Android 8–10.
            root.setFitsSystemWindows(true);
        }
    }

    @SuppressLint("SetJavaScriptEnabled") // The existing Woodshed product requires JavaScript.
    @SuppressWarnings("deprecation")
    private void secureSettings(WebView view, boolean productPage) {
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(productPage);
        settings.setDomStorageEnabled(productPage);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setGeolocationEnabled(false);
        settings.setSafeBrowsingEnabled(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        // Only to intercept user-initiated _blank links; no second browsing window is shown.
        settings.setSupportMultipleWindows(productPage);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);
        CookieManager.getInstance().setAcceptThirdPartyCookies(view, false);
        if (!productPage) settings.setBlockNetworkLoads(true);
    }

    private void createWebView() {
        web = new WebView(this) {
            @Override protected void onWindowVisibilityChanged(int visibility) {
                super.onWindowVisibilityChanged(visibility);
                // Unlike dialog focus/pause changes, a hidden window really leaves the page.
                if (web == this && visibility != View.VISIBLE) suspendMicrophone("window hidden");
            }
        };
        web.setBackgroundColor(getColor(R.color.woodshed_background));
        web.setVisibility(View.INVISIBLE);
        secureSettings(web, true);
        CookieManager.getInstance().setAcceptCookie(true);
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG);
        web.getSettings().setUserAgentString(web.getSettings().getUserAgentString()
                + " WoodshedAndroid/" + BuildConfig.VERSION_NAME);
        web.setWebViewClient(new PageClient());
        web.setWebChromeClient(new ChromeClient());
        container.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
        if (!resumed) web.onPause();
    }

    private void loadTrusted(String url) {
        intendedUrl = urls.retryUrl(url);
        if (web == null) createWebView();
        invalidateAudio("trusted reload");
        web.stopLoading();
        failed = false;
        committed = false;
        statusPanel.setVisibility(View.VISIBLE);
        statusMessage.setText(R.string.loading);
        progress.setVisibility(View.VISIBLE);
        retry.setVisibility(View.GONE);
        web.setVisibility(View.INVISIBLE);
        handler.removeCallbacks(loadTimeout);
        handler.postDelayed(loadTimeout, 45000);
        web.loadUrl(intendedUrl);
    }

    private void showFailure(int message) {
        if (isFinishing() || isDestroyed()) return;
        failed = true;
        committed = false;
        backGeneration++;
        backPending = false;
        invalidateAudio("page failure");
        handler.removeCallbacks(loadTimeout);
        if (web != null) {
            web.stopLoading();
            web.setVisibility(View.INVISIBLE);
        }
        statusPanel.setVisibility(View.VISIBLE);
        statusMessage.setText(message);
        progress.setVisibility(View.GONE);
        retry.setVisibility(View.VISIBLE);
    }

    private boolean isOffline() {
        ConnectivityManager manager = getSystemService(ConnectivityManager.class);
        NetworkCapabilities network = manager.getNetworkCapabilities(manager.getActiveNetwork());
        return network == null || !network.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void openExternal(String url) {
        NavigationPolicy.Destination destination = urls.classify(url);
        Intent intent;
        if (destination == NavigationPolicy.Destination.EXTERNAL_HTTPS) {
            intent = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
            intent.addCategory(Intent.CATEGORY_BROWSABLE);
        } else if (destination == NavigationPolicy.Destination.MAILTO) {
            intent = new Intent(Intent.ACTION_SENDTO, Uri.parse(url));
        } else {
            Toast.makeText(this, R.string.blocked_link, Toast.LENGTH_SHORT).show();
            return;
        }
        try { startActivity(intent); }
        catch (ActivityNotFoundException | SecurityException ignored) {
            Toast.makeText(this, R.string.no_link_app, Toast.LENGTH_LONG).show();
        }
    }

    private final class PageClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            String url = request.getUrl().toString();
            if (urls.isTrusted(url)) return false;
            // A subframe must never launch another application.
            if (request.isForMainFrame()) openExternal(url);
            return true;
        }

        @Override public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            // Also catches main-frame POSTs, which do not call shouldOverrideUrlLoading.
            // Cross-origin POST bodies are never replayed through an external Intent.
            if (request.isForMainFrame() && !urls.isTrusted(request.getUrl().toString())) {
                return new WebResourceResponse("text/plain", "UTF-8", 403, "Blocked",
                        null, new ByteArrayInputStream(new byte[0]));
            }
            return null;
        }

        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            pageGeneration++;
            backGeneration++;
            backPending = false;
            invalidateAudio("main-frame navigation");
            destroyPopup();
            committed = false;
            if (!urls.isTrusted(url)) {
                showFailure(R.string.page_error);
                return;
            }
            intendedUrl = urls.retryUrl(url);
            failed = false;
        }

        @Override public void onPageCommitVisible(WebView view, String url) {
            if (failed || !urls.isTrusted(url) || !urls.isTrusted(view.getUrl())) return;
            committed = true;
            handler.removeCallbacks(loadTimeout);
            statusPanel.setVisibility(View.GONE);
            view.setVisibility(View.VISIBLE);
            CookieManager.getInstance().flush();
        }

        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (!request.isForMainFrame()) return;
            debugCode("main-frame network failure", error.getErrorCode());
            showFailure(isOffline() ? R.string.offline : R.string.connection_error);
        }

        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (!request.isForMainFrame()) return;
            int status = response.getStatusCode();
            debugCode("main-frame HTTP response", status);
            // Keep Woodshed's authentication/authorization response authoritative.
            // Subresource /account/me failures remain entirely in R4A's recovery UI.
            if (status == 401 || status == 403) return;
            showFailure(status >= 500 ? R.string.server_error : R.string.page_error);
        }

        @Override public void onReceivedSslError(WebView view, SslErrorHandler ssl, SslError error) {
            ssl.cancel(); // Never bypass certificate errors, including in debug builds.
            debugCode("TLS failure", error.getPrimaryError());
            if (error.getUrl().equals(view.getUrl()) || error.getUrl().equals(intendedUrl)) {
                showFailure(R.string.secure_error);
            }
        }

        @Override public void onSafeBrowsingHit(WebView view, WebResourceRequest request,
                                               int threatType, SafeBrowsingResponse response) {
            if (Build.VERSION.SDK_INT >= 27) response.backToSafety(true);
            if (request.isForMainFrame()) showFailure(R.string.secure_error);
        }

        @Override public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            debugCode("renderer stopped", detail.didCrash() ? 1 : 0);
            invalidateAudio("renderer destroyed");
            destroyPopup();
            container.removeView(view);
            view.destroy();
            web = null;
            pageGeneration++;
            showFailure(R.string.renderer_error);
            return true;
        }
    }

    private final class ChromeClient extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request) {
            debugAudio("WebView request received");
            applyAudioDecision(microphone.request(request, request.getOrigin().toString(),
                    request.getResources(), pageGeneration, audioPage(), audioForeground(), hasRecordAudio()));
        }

        @Override public void onPermissionRequestCanceled(PermissionRequest request) {
            debugAudio("WebView request canceled");
            microphone.canceled(request); // Already canceled by WebView: do not grant or deny it again.
        }

        @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
            callback.invoke(origin, false, false);
        }

        @Override public boolean onConsoleMessage(ConsoleMessage message) {
            // Web console messages may contain account data. Do not relay them to logcat.
            return true;
        }

        @Override public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, Message message) {
            if (!userGesture || !resumed || !committed || !urls.isTrusted(view.getUrl())) return false;
            destroyPopup();
            final int generation = pageGeneration;
            WebView router = new WebView(MainActivity.this);
            popupRouter = router;
            secureSettings(router, false);
            router.setWebViewClient(new WebViewClient() {
                private boolean handled;
                private void route(String url) {
                    if (handled) return;
                    handled = true;
                    handler.post(() -> {
                        if (popupRouter != router) return;
                        destroyPopup();
                        if (!resumed || generation != pageGeneration || web == null
                                || !urls.isTrusted(web.getUrl())) return;
                        if (urls.isTrusted(url)) web.loadUrl(url);
                        else openExternal(url);
                    });
                }
                @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                    if (request.isForMainFrame()) route(request.getUrl().toString());
                    return true;
                }
                @Override public void onPageStarted(WebView v, String url, android.graphics.Bitmap icon) {
                    v.stopLoading();
                    route(url);
                }
                @Override public boolean onRenderProcessGone(WebView v, RenderProcessGoneDetail detail) {
                    if (popupRouter == v) destroyPopup();
                    return true;
                }
            });
            // This detached, network/JS-disabled router never presents or executes a page.
            ((WebView.WebViewTransport) message.obj).setWebView(router);
            message.sendToTarget();
            handler.postDelayed(closePopup, 5000);
            return true;
        }
    }

    private void destroyPopup() {
        handler.removeCallbacks(closePopup);
        if (popupRouter == null) return;
        popupRouter.stopLoading();
        popupRouter.destroy();
        popupRouter = null;
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(code, permissions, grants);
        if (code != AUDIO_PERMISSION) return;
        boolean granted = permissions.length == 1 && Manifest.permission.RECORD_AUDIO.equals(permissions[0])
                && grants.length == 1 && grants[0] == PackageManager.PERMISSION_GRANTED && hasRecordAudio();
        debugAudio(granted ? "Android callback granted" : "Android callback denied or dismissed");
        applyAudioDecision(microphone.nativeResult(granted, pageGeneration, audioPage(), audioForeground()));
    }

    private void completePendingAudio() {
        applyAudioDecision(microphone.complete(pageGeneration, audioPage(), audioForeground(), hasRecordAudio()));
    }

    private String audioPage() { return web == null ? null : web.getUrl(); }

    private boolean audioWindowVisible() { return web != null && web.getWindowVisibility() == View.VISIBLE; }

    private boolean audioForeground() {
        return resumed && committed && audioWindowVisible() && hasWindowFocus()
                && !isFinishing() && !isDestroyed();
    }

    private boolean hasRecordAudio() {
        return checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
    }

    private void applyAudioDecision(MicrophonePermissionFlow.Decision<PermissionRequest> decision) {
        debugAudio(decision.reason);
        switch (decision.action) {
            case GRANT:
                // Recheck the actual Android request at the grant boundary, on the UI thread.
                if (audioForeground() && urls.isTrusted(audioPage()) && hasRecordAudio()
                        && AudioPermissionPolicy.permits(urls, decision.request.getOrigin().toString(),
                        decision.request.getResources())) {
                    decision.request.grant(new String[]{PermissionRequest.RESOURCE_AUDIO_CAPTURE});
                    debugAudio("WebView audio granted");
                } else {
                    decision.request.deny();
                    debugAudio("WebView request denied at final validation");
                }
                break;
            case DENY:
                decision.request.deny();
                debugAudio("WebView request denied");
                break;
            case REQUEST_ANDROID:
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, AUDIO_PERMISSION);
                break;
            case NONE: break;
        }
    }

    private void invalidateAudio(String reason) { applyAudioDecision(microphone.invalidate(reason)); }

    private void suspendMicrophone(String reason) {
        invalidateAudio(reason);
        if (web != null) web.onPause(); // R4A visibility cleanup stops existing capture; never auto-restart it.
    }

    @SuppressLint("GestureBackNavigation") // API 26–32 fallback; API 33+ registers the platform dispatcher.
    @SuppressWarnings("deprecation")
    @Override public void onBackPressed() { handleBack(); }

    private void handleBack() {
        if (backPending || !resumed) return;
        if (web == null || !committed || !urls.isTrusted(web.getUrl())) {
            finishBack("false");
            return;
        }
        backPending = true;
        final int generation = pageGeneration;
        final int backRequest = ++backGeneration;
        final String currentUrl = web.getUrl();
        web.evaluateJavascript(BackPolicy.DISMISS_SCRIPT, result -> {
            if (backRequest != backGeneration) return;
            backPending = false;
            if (!resumed || web == null || generation != pageGeneration
                    || !currentUrl.equals(web.getUrl()) || !urls.isTrusted(web.getUrl())) return;
            finishBack(result);
        });
    }

    private int internalHistoryOffset() {
        if (web == null) return 0;
        WebBackForwardList history = web.copyBackForwardList();
        for (int index = history.getCurrentIndex() - 1; index >= 0; index--) {
            if (urls.isTrusted(history.getItemAtIndex(index).getUrl())) return index - history.getCurrentIndex();
        }
        return 0;
    }

    private void finishBack(String result) {
        int offset = internalHistoryOffset();
        switch (BackPolicy.afterDismiss(result, offset != 0)) {
            case STAY: break;
            case HISTORY:
                failed = false;
                web.goBackOrForward(offset);
                break;
            case EXIT: finish(); break;
        }
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        if (web != null) web.onResume();
        completePendingAudio();
    }

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) completePendingAudio();
    }

    @Override protected void onPause() {
        resumed = false;
        backGeneration++;
        backPending = false;
        destroyPopup();
        // Pausing WebView hides the DOM page and closes R4A's tuner. Do not do that
        // for our visible runtime-permission overlay, including callback-before-resume.
        if (microphone.keepPageActive(audioWindowVisible()) && !isFinishing()) {
            debugAudio("permission overlay pause preserved");
        } else {
            suspendMicrophone("Activity paused outside permission overlay");
        }
        CookieManager.getInstance().flush();
        super.onPause();
    }

    @Override protected void onStop() {
        if (microphone.keepPageActive(audioWindowVisible()) && !isFinishing()) {
            debugAudio("permission overlay stop with visible window preserved");
        } else {
            suspendMicrophone("Activity stopped or window hidden");
        }
        super.onStop();
    }

    @Override protected void onDestroy() {
        invalidateAudio("Activity destroyed");
        destroyPopup();
        handler.removeCallbacksAndMessages(null);
        if (Build.VERSION.SDK_INT >= 33 && backCallback != null) {
            getOnBackInvokedDispatcher().unregisterOnBackInvokedCallback(backCallback);
        }
        if (web != null) {
            container.removeView(web);
            web.stopLoading();
            web.setWebChromeClient(null);
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    private static void debugCode(String event, int code) {
        if (BuildConfig.DEBUG) Log.d("WoodshedShell", event + " (" + code + ")");
    }

    private static void debugAudio(String event) {
        if (BuildConfig.DEBUG) Log.d("WoodshedMic", event); // Fixed states/reasons only; no URLs or account data.
    }
}
