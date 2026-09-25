package com.woodshedwoodchuck.app;

import org.junit.Test;
import java.io.File;
import java.util.HashSet;
import java.util.Set;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.NodeList;
import static org.junit.Assert.*;

public class AudioPermissionPolicyTest {
    private final NavigationPolicy urls = new NavigationPolicy(ShellConfig.ORIGIN);
    private static final String VIDEO = "android.webkit.resource.VIDEO_CAPTURE";

    @Test public void trustedMicrophoneOnly() {
        assertTrue(AudioPermissionPolicy.permits(urls, ShellConfig.ORIGIN, new String[]{AudioPermissionPolicy.AUDIO}));
    }

    @Test public void cameraUnknownAndMixedRequestsAreDeniedEntirely() {
        for (String[] resources : new String[][]{null, {}, {VIDEO}, {"unknown"},
                {AudioPermissionPolicy.AUDIO, VIDEO}, {AudioPermissionPolicy.AUDIO, "unknown"}}) {
            assertFalse(AudioPermissionPolicy.permits(urls, ShellConfig.ORIGIN, resources));
        }
    }

    @Test public void untrustedOriginsCannotCaptureAudio() {
        for (String origin : new String[]{"http://woodshed-woodchuck.onrender.com",
                ShellConfig.ORIGIN + ".evil.example", "https://evil.example", "null", null,
                ShellConfig.ORIGIN + ":444"}) {
            assertFalse(AudioPermissionPolicy.permits(urls, origin, new String[]{AudioPermissionPolicy.AUDIO}));
        }
    }

    @Test public void manifestIncludesChromiumCapturePrerequisitesWithoutCamera() throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        NodeList permissions = factory.newDocumentBuilder().parse(new File("src/main/AndroidManifest.xml"))
                .getElementsByTagName("uses-permission");
        Set<String> names = new HashSet<>();
        for (int i = 0; i < permissions.getLength(); i++) {
            names.add(permissions.item(i).getAttributes()
                    .getNamedItemNS("http://schemas.android.com/apk/res/android", "name").getNodeValue());
        }
        assertTrue(names.contains("android.permission.RECORD_AUDIO"));
        assertTrue(names.contains("android.permission.MODIFY_AUDIO_SETTINGS"));
        assertFalse(names.contains("android.permission.CAMERA"));
    }
}
