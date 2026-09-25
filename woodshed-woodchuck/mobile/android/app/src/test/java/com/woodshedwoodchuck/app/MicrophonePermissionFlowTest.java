package com.woodshedwoodchuck.app;

import org.junit.Test;
import static org.junit.Assert.*;
import static com.woodshedwoodchuck.app.MicrophonePermissionFlow.Action.*;

/** Tests native decisions/identity, not a simulated WebView, microphone or OS permission dialog. */
public class MicrophonePermissionFlowTest {
    private static final String PAGE = ShellConfig.ORIGIN + "/home";
    private static final String[] AUDIO = {AudioPermissionPolicy.AUDIO};
    private final MicrophonePermissionFlow<Object> flow =
            new MicrophonePermissionFlow<>(new NavigationPolicy(ShellConfig.ORIGIN));
    private final Object original = new Object();

    private MicrophonePermissionFlow.Decision<Object> request(boolean granted) {
        return flow.request(original, ShellConfig.ORIGIN, AUDIO, 1, PAGE, true, granted);
    }

    @Test public void alreadyGrantedAndroidPermissionGrantsAudioImmediately() {
        MicrophonePermissionFlow.Decision<Object> decision = request(true);
        assertEquals(GRANT, decision.action);
        assertSame(original, decision.request);
        assertFalse(flow.keepPageActive(true)); // No runtime overlay is in progress.
    }

    @Test public void absentPermissionRetainsOriginalAndRequestsAndroid() {
        MicrophonePermissionFlow.Decision<Object> decision = request(false);
        assertEquals(REQUEST_ANDROID, decision.action);
        assertSame(original, decision.request);
        assertTrue(flow.keepPageActive(true));
        assertEquals(NONE, flow.complete(1, PAGE, true, false).action);
    }

    @Test public void nativeGrantCompletesOriginalExactlyOnce() {
        request(false);
        MicrophonePermissionFlow.Decision<Object> decision = flow.nativeResult(true, 1, PAGE, true);
        assertEquals(GRANT, decision.action);
        assertSame(original, decision.request);
        assertEquals(NONE, flow.complete(1, PAGE, true, true).action);
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, true).action);
    }

    @Test public void nativeDenialDeniesAndClearsWithoutAnotherPrompt() {
        request(false);
        MicrophonePermissionFlow.Decision<Object> decision = flow.nativeResult(false, 1, PAGE, false);
        assertEquals(DENY, decision.action);
        assertSame(original, decision.request);
        assertFalse(flow.keepPageActive(true));
        assertEquals(NONE, flow.complete(1, PAGE, true, false).action);
    }

    @Test public void cameraMixedAndUnknownResourcesAreDeniedEvenWithAndroidPermission() {
        for (String[] resources : new String[][]{{"android.webkit.resource.VIDEO_CAPTURE"},
                {AudioPermissionPolicy.AUDIO, "android.webkit.resource.VIDEO_CAPTURE"}, {"unknown"}, {}, null}) {
            assertEquals(DENY, flow.request(original, ShellConfig.ORIGIN, resources,
                    1, PAGE, true, true).action);
        }
        assertFalse(flow.keepPageActive(true));
    }

    @Test public void bothRequestingOriginAndMainPageMustBeTrusted() {
        for (String origin : new String[]{"https://evil.example", ShellConfig.ORIGIN + ".evil.example",
                "http://woodshed-woodchuck.onrender.com", ShellConfig.ORIGIN + ":444"}) {
            assertEquals(DENY, flow.request(original, origin, AUDIO, 1, PAGE, true, true).action);
            assertEquals(DENY, flow.request(original, ShellConfig.ORIGIN, AUDIO, 1, origin, true, true).action);
        }
    }

    @Test public void backgroundRequestIsDeniedEvenWithAndroidPermission() {
        assertEquals(DENY, flow.request(original, ShellConfig.ORIGIN, AUDIO, 1, PAGE, false, true).action);
    }

    @Test public void cancellationNeverGrantsOrDeniesCanceledRequestLater() {
        request(false);
        flow.canceled(original);
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, true).action);
        assertEquals(NONE, flow.complete(1, PAGE, true, true).action);
        assertEquals(NONE, flow.invalidate("destruction").action);
    }

    @Test public void cancelingAnotherRequestDoesNotCancelOriginal() {
        request(false);
        flow.canceled(new Object());
        assertSame(original, flow.nativeResult(true, 1, PAGE, true).request);
    }

    @Test public void duplicateRequestCannotReplaceOriginalOrLaunchSecondDialog() {
        request(false);
        Object duplicate = new Object();
        MicrophonePermissionFlow.Decision<Object> denied =
                flow.request(duplicate, ShellConfig.ORIGIN, AUDIO, 1, PAGE, true, false);
        assertEquals(DENY, denied.action);
        assertSame(duplicate, denied.request);
        assertSame(original, flow.nativeResult(true, 1, PAGE, true).request);
    }

    @Test public void canceledDialogResultCannotBeReassignedToANewerRequest() {
        request(false);
        flow.canceled(original);
        Object newer = new Object();
        assertEquals(DENY, flow.request(newer, ShellConfig.ORIGIN, AUDIO, 1, PAGE, true, true).action);
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, true).action);
        assertEquals(GRANT, flow.request(newer, ShellConfig.ORIGIN, AUDIO, 1, PAGE, true, true).action);
    }

    @Test public void realNavigationDestructionAndBackgroundInvalidateOriginal() {
        for (String reason : new String[]{"main-frame navigation", "Activity destroyed", "window hidden", "renderer destroyed"}) {
            request(false);
            MicrophonePermissionFlow.Decision<Object> decision = flow.invalidate(reason);
            assertEquals(DENY, decision.action);
            assertSame(original, decision.request);
            assertFalse(flow.keepPageActive(true));
            assertEquals(NONE, flow.nativeResult(true, 1, PAGE, true).action);
            assertEquals(NONE, flow.complete(1, PAGE, true, true).action);
        }
    }

    @Test public void changedPageOrGenerationRejectsEvenIfInvalidationCallbackWasMissed() {
        request(false);
        assertEquals(DENY, flow.nativeResult(true, 2, PAGE, true).action);
        request(false);
        assertEquals(DENY, flow.nativeResult(true, 1, ShellConfig.ORIGIN + "/shop", true).action);
        request(false);
        assertEquals(DENY, flow.nativeResult(true, 1, "https://evil.example/", true).action);
    }

    @Test public void visiblePermissionOverlayPauseOrStopPreservesOriginal() {
        request(false);
        assertTrue(flow.keepPageActive(true)); // Activity pause with visible OS permission overlay.
        assertTrue(flow.keepPageActive(true)); // Even a stop notification alone must not discard it.
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, false).action);
        assertTrue(flow.keepPageActive(true)); // Result arrived before resume/focus, same overlay transition.
        assertEquals(NONE, flow.complete(1, PAGE, false, true).action); // Resume without focus.
        MicrophonePermissionFlow.Decision<Object> decision = flow.complete(1, PAGE, true, true);
        assertEquals(GRANT, decision.action);
        assertSame(original, decision.request);
    }

    @Test public void resumeBeforeNativeCallbackWaitsThenGrantsOriginal() {
        request(false);
        assertEquals(NONE, flow.complete(1, PAGE, true, true).action);
        assertEquals(GRANT, flow.nativeResult(true, 1, PAGE, true).action);
    }

    @Test public void hiddenWindowIsNotExemptEvenWhenPermissionDialogIsOpen() {
        request(false);
        assertFalse(flow.keepPageActive(false));
        assertEquals(DENY, flow.invalidate("window hidden").action);
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, false).action);
        assertEquals(NONE, flow.complete(1, PAGE, true, true).action); // No silent restart.
    }

    @Test public void leavingAfterNativeGrantBeforeFocusReturnsCannotStartMicrophone() {
        request(false);
        assertEquals(NONE, flow.nativeResult(true, 1, PAGE, false).action);
        assertEquals(DENY, flow.invalidate("window hidden").action);
        assertEquals(NONE, flow.complete(1, PAGE, true, true).action);
    }

    @Test public void permissionRevokedBeforeDeferredGrantIsDenied() {
        request(false);
        flow.nativeResult(true, 1, PAGE, false);
        assertEquals(DENY, flow.complete(1, PAGE, true, false).action);
        assertFalse(flow.keepPageActive(true));
    }
}
