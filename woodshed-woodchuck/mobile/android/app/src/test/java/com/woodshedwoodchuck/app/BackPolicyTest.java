package com.woodshedwoodchuck.app;

import org.junit.Test;
import static org.junit.Assert.*;
import static com.woodshedwoodchuck.app.BackPolicy.Action.*;

public class BackPolicyTest {
    @Test public void dismissedSurfaceTakesPrecedenceOverHistoryOrExit() {
        assertEquals(STAY, BackPolicy.afterDismiss("true", true));
        assertEquals(STAY, BackPolicy.afterDismiss("true", false));
    }

    @Test public void historyPrecedesExitWhenNoSurfaceWasDismissed() {
        assertEquals(HISTORY, BackPolicy.afterDismiss("false", true));
        assertEquals(EXIT, BackPolicy.afterDismiss("false", false));
    }

    @Test public void missingContractDoesNotTrapBack() {
        for (String result : new String[]{"null", null, "", "\"true\""}) {
            assertEquals(HISTORY, BackPolicy.afterDismiss(result, true));
            assertEquals(EXIT, BackPolicy.afterDismiss(result, false));
        }
    }
}
