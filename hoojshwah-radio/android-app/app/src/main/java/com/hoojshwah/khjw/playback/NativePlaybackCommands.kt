package com.hoojshwah.khjw.playback

import android.os.Bundle
import androidx.media3.session.SessionCommand

object NativePlaybackCommands {
    enum class Album5PreviewAction { QUEUE, EXIT }

    const val ARG_TRACK_ID = "track_id"
    const val WEB_BACKSTAGE_PREFIX = "backstage-track:"
    const val WEB_ALBUM5_PREVIEW_QUEUE = "album5-preview:queue"
    const val WEB_ALBUM5_PREVIEW_EXIT = "album5-preview:exit"

    val selectBackstageTrack: SessionCommand by lazy {
        SessionCommand(
            "com.hoojshwah.khjw.command.SELECT_BACKSTAGE_TRACK",
            Bundle.EMPTY,
        )
    }

    val queueAlbum5Preview: SessionCommand by lazy {
        SessionCommand("com.hoojshwah.khjw.command.QUEUE_ALBUM5_PREVIEW", Bundle.EMPTY)
    }

    val exitAlbum5Preview: SessionCommand by lazy {
        SessionCommand("com.hoojshwah.khjw.command.EXIT_ALBUM5_PREVIEW", Bundle.EMPTY)
    }

    fun parseBackstageTrackId(message: String): String? {
        if (!message.startsWith(WEB_BACKSTAGE_PREFIX)) return null

        return message.removePrefix(WEB_BACKSTAGE_PREFIX)
            .takeIf { it.matches(Regex("[A-Za-z0-9._-]{1,80}")) }
    }

    fun parseAlbum5PreviewAction(message: String): Album5PreviewAction? = when (message) {
        WEB_ALBUM5_PREVIEW_QUEUE -> Album5PreviewAction.QUEUE
        WEB_ALBUM5_PREVIEW_EXIT -> Album5PreviewAction.EXIT
        else -> null
    }
}
