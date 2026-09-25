package com.woodshedwoodchuck.app;

import java.net.URI;
import java.net.URISyntaxException;

/** No Android dependencies: fail closed on malformed or ambiguous URLs. */
final class NavigationPolicy {
    enum Destination { INTERNAL, EXTERNAL_HTTPS, MAILTO, BLOCKED }
    private final URI origin;

    NavigationPolicy(String configuredOrigin) {
        origin = parse(configuredOrigin);
        if (!isHttps(origin) || origin.getRawQuery() != null || origin.getRawFragment() != null
                || !(origin.getRawPath().isEmpty() || origin.getRawPath().equals("/"))) {
            throw new IllegalArgumentException("Shell origin must be an HTTPS origin");
        }
    }

    Destination classify(String value) {
        URI uri = parse(value);
        if (uri == null) return Destination.BLOCKED;
        if (isHttps(uri)) {
            return origin.getHost().equalsIgnoreCase(uri.getHost())
                    && effectivePort(origin) == effectivePort(uri)
                    ? Destination.INTERNAL : Destination.EXTERNAL_HTTPS;
        }
        if ("mailto".equalsIgnoreCase(uri.getScheme()) && uri.isOpaque()
                && uri.getRawFragment() == null && !uri.getRawSchemeSpecificPart().isEmpty()
                && !uri.getRawSchemeSpecificPart().startsWith("//")) {
            return Destination.MAILTO;
        }
        return Destination.BLOCKED;
    }

    boolean isTrusted(String value) { return classify(value) == Destination.INTERNAL; }

    /** Used for in-memory retry destinations; no last URL or credentials are persisted. */
    String safePath(String value) {
        URI path = parse(value);
        if (path == null || path.isAbsolute() || path.getRawAuthority() != null
                || !value.startsWith("/") || value.startsWith("//")
                || path.getRawFragment() != null || !isTrusted(originString() + value)) return "/";
        // Reject encoded separators/control characters at a restoration boundary as well.
        String decoded = path.getPath();
        if (decoded == null || decoded.startsWith("//") || decoded.indexOf('\\') >= 0
                || hasControls(decoded)) return "/";
        return value;
    }

    String retryUrl(String trustedUrl) {
        if (!isTrusted(trustedUrl)) return originString() + "/";
        URI uri = parse(trustedUrl);
        String path = uri.getRawPath().isEmpty() ? "/" : uri.getRawPath();
        if (uri.getRawQuery() != null) path += "?" + uri.getRawQuery();
        return originString() + safePath(path);
    }

    private String originString() {
        return "https://" + origin.getRawAuthority();
    }

    private static URI parse(String value) {
        if (value == null || value.isEmpty() || value.indexOf('\\') >= 0 || hasControls(value)) return null;
        try { return new URI(value); }
        catch (URISyntaxException ignored) { return null; }
    }

    private static boolean hasControls(String value) {
        for (int i = 0; i < value.length(); i++) {
            if (Character.isISOControl(value.charAt(i))) return true;
        }
        return false;
    }

    private static boolean isHttps(URI uri) {
        return uri != null && "https".equalsIgnoreCase(uri.getScheme()) && !uri.isOpaque()
                && uri.getHost() != null && uri.getRawUserInfo() == null
                && uri.getPort() != 0 && uri.getPort() <= 65535
                && !uri.getRawAuthority().endsWith(":");
    }

    private static int effectivePort(URI uri) { return uri.getPort() == -1 ? 443 : uri.getPort(); }
}
