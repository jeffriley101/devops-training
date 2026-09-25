package com.woodshedwoodchuck.app;

import org.junit.Test;
import static org.junit.Assert.*;
import static com.woodshedwoodchuck.app.NavigationPolicy.Destination.*;

public class NavigationPolicyTest {
    private final NavigationPolicy policy = new NavigationPolicy(ShellConfig.ORIGIN);

    @Test public void exactHttpsOriginStaysInternal() {
        for (String url : new String[]{ShellConfig.ORIGIN + "/", ShellConfig.ORIGIN + "/home",
                ShellConfig.ORIGIN + "/arcade?x=1", ShellConfig.ORIGIN + ":443/home",
                "https://WOODSHED-WOODCHUCK.ONRENDER.COM/home"}) {
            assertEquals(url, INTERNAL, policy.classify(url));
        }
    }

    @Test public void externalHttpsNeverStaysInWebView() {
        for (String url : new String[]{
                ShellConfig.ORIGIN + ".evil.example/",
                "https://evil.example/?next=" + ShellConfig.ORIGIN + "/",
                ShellConfig.ORIGIN + ":444/home", "https://example.com/",
                "https://woodshed-woodchuck.onrender.com./"}) {
            assertFalse(url, policy.isTrusted(url));
            assertEquals(url, EXTERNAL_HTTPS, policy.classify(url));
        }
    }

    @Test public void unsupportedAndAmbiguousUrlsAreBlocked() {
        for (String url : new String[]{
                "http://woodshed-woodchuck.onrender.com/", "javascript:alert(1)",
                "file:///tmp/test.html", "content://example/item", "data:text/html,test",
                "intent://example", "tel:+15555555555", "/home", "//example.com/", "",
                "https://user@woodshed-woodchuck.onrender.com/", "https://example.com\\@" + ShellConfig.ORIGIN,
                "https://woodshed-woodchuck.onrender.com@evil.example/", "https://example.com:0/",
                "https://example.com:65536/", "https://example.com:/", "https://example.com/%zz",
                "https://example.com/\n", " https://example.com/", "https:///example.com/", null}) {
            assertEquals(url, BLOCKED, policy.classify(url));
        }
    }

    @Test public void supportEmailIsExternal() {
        assertEquals(MAILTO, policy.classify("mailto:support@woodshedwoodchuck.com"));
        assertEquals(MAILTO, policy.classify("mailto:woodshedwoodchuck@gmail.com?subject=Help"));
        assertEquals(BLOCKED, policy.classify("mailto:"));
        assertEquals(BLOCKED, policy.classify("mailto://example.com"));
    }

    @Test public void onlySafeRelativePathsCanBeRestored() {
        assertEquals("/home", policy.safePath("/home"));
        assertEquals("/arcade?x=1", policy.safePath("/arcade?x=1"));
        for (String path : new String[]{"https://evil.example/", ShellConfig.ORIGIN + "/home",
                "//evil.example/", "///evil.example/", "/\\evil.example/", "/%2Fevil.example/",
                "/%5Cevil.example/", "/%00", "/%0A", "home", "", "/%xx", null,
                "javascript:alert(1)", "/home#fragment", "/\n"}) {
            assertEquals(String.valueOf(path), "/", policy.safePath(path));
        }
    }

    @Test public void retryUsesOnlyTrustedPathAndQueryAndDropsFragment() {
        assertEquals(ShellConfig.ORIGIN + "/arcade?x=1",
                policy.retryUrl(ShellConfig.ORIGIN + "/arcade?x=1#panel"));
        assertEquals(ShellConfig.ORIGIN + "/", policy.retryUrl("https://evil.example/"));
        assertEquals(ShellConfig.ORIGIN + "/", policy.retryUrl(null));
    }

    @Test public void originConfigurationMustBeAnHttpsOrigin() {
        for (String origin : new String[]{"http://example.com", "https://example.com/path",
                "https://user@example.com", "https://example.com?q=1", "https://example.com#x", null}) {
            assertThrows(IllegalArgumentException.class, () -> new NavigationPolicy(origin));
        }
        NavigationPolicy custom = new NavigationPolicy("https://example.com:8443");
        assertTrue(custom.isTrusted("https://example.com:8443/home"));
        assertFalse(custom.isTrusted("https://example.com/home"));
    }
}
