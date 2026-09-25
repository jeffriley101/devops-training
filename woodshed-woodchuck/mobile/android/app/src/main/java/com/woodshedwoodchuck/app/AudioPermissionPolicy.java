package com.woodshedwoodchuck.app;

final class AudioPermissionPolicy {
    // Equal to PermissionRequest.RESOURCE_AUDIO_CAPTURE, kept pure for JVM tests.
    static final String AUDIO = "android.webkit.resource.AUDIO_CAPTURE";

    static boolean permits(NavigationPolicy urls, String origin, String[] resources) {
        return urls.isTrusted(origin) && resources != null && resources.length == 1
                && AUDIO.equals(resources[0]);
    }

    private AudioPermissionPolicy() {}
}
