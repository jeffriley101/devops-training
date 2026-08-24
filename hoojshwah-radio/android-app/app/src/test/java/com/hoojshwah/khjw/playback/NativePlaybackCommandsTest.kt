package com.hoojshwah.khjw.playback

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class NativePlaybackCommandsTest {
    @Test
    fun parsesNarrowBackstageTrackMessage() {
        assertEquals(
            "track-id_2026.1",
            NativePlaybackCommands.parseBackstageTrackId("backstage-track:track-id_2026.1"),
        )
    }

    @Test
    fun rejectsOtherBridgeMessagesAndInvalidTrackIds() {
        assertNull(NativePlaybackCommands.parseBackstageTrackId("play"))
        assertNull(NativePlaybackCommands.parseBackstageTrackId("backstage-track:"))
        assertNull(NativePlaybackCommands.parseBackstageTrackId("backstage-track:../secret"))
    }
}
