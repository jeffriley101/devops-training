package com.hoojshwah.khjw.playback

import com.hoojshwah.khjw.data.StationTrack
import org.junit.Assert.assertEquals
import org.junit.Test

class StationPositionTest {
    private val tracks = listOf(
        StationTrack("one", "One", "Hoojshwah", "https://example.test/one.mp3", 30.0),
        StationTrack("two", "Two", "Hoojshwah", "https://example.test/two.mp3", 45.0),
    )

    @Test
    fun calculatesTrackAndOffsetFromUnixTimeModuloLoop() {
        val position = StationPositionCalculator.calculate(
            tracks = tracks,
            totalDurationSeconds = 75.0,
            unixTimeMilliseconds = 40_250L,
        )

        assertEquals(1, position.trackIndex)
        assertEquals(10_250L, position.offsetMilliseconds)
    }

    @Test
    fun wrapsAtTheEndOfTheStationLoop() {
        val position = StationPositionCalculator.calculate(
            tracks = tracks,
            totalDurationSeconds = 75.0,
            unixTimeMilliseconds = 80_000L,
        )

        assertEquals(0, position.trackIndex)
        assertEquals(5_000L, position.offsetMilliseconds)
    }
}

