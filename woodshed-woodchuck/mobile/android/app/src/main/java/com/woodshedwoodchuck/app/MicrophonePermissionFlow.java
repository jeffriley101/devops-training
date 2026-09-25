package com.woodshedwoodchuck.app;

/** UI-thread state only. T is the original WebView request, never a replacement request. */
final class MicrophonePermissionFlow<T> {
    enum Action { NONE, GRANT, DENY, REQUEST_ANDROID }

    static final class Decision<T> {
        final Action action;
        final T request;
        final String reason;

        Decision(Action action, T request, String reason) {
            this.action = action;
            this.request = request;
            this.reason = reason;
        }
    }

    private final NavigationPolicy urls;
    private T pending;
    private int generation;
    private String page;
    private boolean runtimeRequestInFlight;
    private boolean approved;

    MicrophonePermissionFlow(NavigationPolicy urls) { this.urls = urls; }

    Decision<T> request(T request, String origin, String[] resources, int currentGeneration,
                        String currentPage, boolean foreground, boolean androidGranted) {
        if (!foreground || !urls.isTrusted(currentPage)
                || !AudioPermissionPolicy.permits(urls, origin, resources)) {
            return new Decision<>(Action.DENY, request, "request not eligible");
        }
        // Do not overwrite the request waiting on the OS, even after its cancellation.
        if (pending != null || runtimeRequestInFlight) {
            return new Decision<>(Action.DENY, request, "overlapping request");
        }
        if (androidGranted) {
            return new Decision<>(Action.GRANT, request, "Android permission already granted");
        }
        pending = request;
        generation = currentGeneration;
        page = currentPage;
        approved = false;
        runtimeRequestInFlight = true;
        return new Decision<>(Action.REQUEST_ANDROID, request, "requesting Android permission");
    }

    Decision<T> nativeResult(boolean granted, int currentGeneration, String currentPage,
                             boolean foreground) {
        if (!runtimeRequestInFlight) return none("unexpected Android result ignored");
        runtimeRequestInFlight = false;
        if (!granted) return invalidate("Android permission denied or dismissed");
        if (pending == null) return none("Android grant has no surviving request");
        approved = true;
        return complete(currentGeneration, currentPage, foreground, true);
    }

    Decision<T> complete(int currentGeneration, String currentPage, boolean foreground,
                         boolean androidGranted) {
        if (pending == null || !approved) return none("no approved request waiting");
        if (generation != currentGeneration || !page.equals(currentPage)
                || !urls.isTrusted(currentPage) || !androidGranted) {
            return invalidate("page or Android permission changed");
        }
        // The result may precede onResume AND window focus returning from the dialog.
        if (!foreground) return none("Android grant waiting for foreground");
        T request = clearPending();
        return new Decision<>(Action.GRANT, request, "Android grant delivered to original request");
    }

    boolean keepPageActive(boolean windowVisible) {
        // A visible permission overlay is not app backgrounding. A hidden window is.
        return windowVisible && pending != null && (runtimeRequestInFlight || approved);
    }

    void canceled(T request) {
        if (pending == request) clearPending();
        // Keep the in-flight OS result reserved; it must never grant a newer request.
    }

    Decision<T> invalidate(String reason) {
        T request = clearPending();
        return request == null ? none(reason) : new Decision<>(Action.DENY, request, reason);
    }

    private T clearPending() {
        T request = pending;
        pending = null;
        page = null;
        approved = false;
        return request;
    }

    private Decision<T> none(String reason) { return new Decision<>(Action.NONE, null, reason); }
}
