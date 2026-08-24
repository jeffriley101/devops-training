package com.hoojshwah.khjw.playback

import android.os.Bundle
import androidx.media3.session.SessionCommand

object NativePlaybackCommands {
    const val ARG_TRACK_ID = "track_id"
    const val WEB_BACKSTAGE_PREFIX = "backstage-track:"

    val selectBackstageTrack: SessionCommand by lazy {
        SessionCommand(
            "com.hoojshwah.khjw.command.SELECT_BACKSTAGE_TRACK",
            Bundle.EMPTY,
        )
    }

    fun parseBackstageTrackId(message: String): String? {
        if (!message.startsWith(WEB_BACKSTAGE_PREFIX)) return null

        return message.removePrefix(WEB_BACKSTAGE_PREFIX)
            .takeIf { it.matches(Regex("[A-Za-z0-9._-]{1,80}")) }
    }
}
