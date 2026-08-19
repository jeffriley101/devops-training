package com.hoojshwah.khjw.playback

import com.hoojshwah.khjw.data.StationTrack

data class StationPosition(
    val trackIndex: Int,
    val offsetMilliseconds: Long,
)

object StationPositionCalculator {
    fun calculate(
        tracks: List<StationTrack>,
        totalDurationSeconds: Double,
        unixTimeMilliseconds: Long = System.currentTimeMillis(),
    ): StationPosition {
        require(tracks.isNotEmpty()) { "A station position requires at least one track" }

        val trackDurationTotal = tracks.sumOf { it.durationSeconds.coerceAtLeast(0.0) }
        val loopDuration = totalDurationSeconds.takeIf { it > 0.0 } ?: trackDurationTotal
        require(loopDuration > 0.0) { "Station duration must be positive" }

        var loopPosition = (unixTimeMilliseconds / 1000.0) % loopDuration
        if (loopPosition < 0.0) loopPosition += loopDuration

        tracks.forEachIndexed { index, track ->
            val duration = track.durationSeconds.coerceAtLeast(0.0)
            if (loopPosition < duration) {
                return StationPosition(
                    trackIndex = index,
                    offsetMilliseconds = (loopPosition * 1000.0).toLong(),
                )
            }
            loopPosition -= duration
        }

        // A mismatched API total must not crash playback; restart the available loop.
        return StationPosition(trackIndex = 0, offsetMilliseconds = 0L)
    }
}

