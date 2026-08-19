package com.hoojshwah.khjw.playback

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import com.hoojshwah.khjw.data.Station
import com.hoojshwah.khjw.data.StationRepository
import com.hoojshwah.khjw.data.StationTrack

@OptIn(UnstableApi::class)
class KhjwPlaybackService : MediaSessionService() {
    private lateinit var player: ExoPlayer
    private lateinit var mediaSession: MediaSession
    private val repository = StationRepository()
    private val mainHandler = Handler(Looper.getMainLooper())

    private var playableTracks: List<StationTrack> = emptyList()
    private var synchronizationDurationSeconds = 0.0
    private var hasUserStartedPlayback = false
    private var errorSkipsRemaining = 1

    override fun onCreate() {
        super.onCreate()

        player = ExoPlayer.Builder(this)
            .setWakeMode(C.WAKE_MODE_NETWORK)
            .setHandleAudioBecomingNoisy(true)
            .build().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(C.USAGE_MEDIA)
                        .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                        .build(),
                    true,
                )
                repeatMode = Player.REPEAT_MODE_ALL
                addListener(playerListener)
            }

        mediaSession = MediaSession.Builder(this, player).build()

        updateStatus("Loading the KHJW station…")
        repository.loadStation { result ->
            mainHandler.post { handleStationResult(result) }
        }
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession = mediaSession

    override fun onDestroy() {
        repository.close()
        mediaSession.release()
        player.release()
        super.onDestroy()
    }

    private fun handleStationResult(result: Result<Station>) {
        result.fold(
            onSuccess = { station -> configureStation(station) },
            onFailure = { error ->
                updateStatus(error.message ?: "KHJW is temporarily unavailable")
            },
        )
    }

    private fun configureStation(station: Station) {
        playableTracks = station.tracks.filter {
            it.durationSeconds > 0.0 && !it.audioUrl.isNullOrBlank()
        }

        if (playableTracks.isEmpty()) {
            updateStatus("The station has no playable tracks right now")
            return
        }

        val allTracksArePlayable = playableTracks.size == station.tracks.size
        synchronizationDurationSeconds = if (allTracksArePlayable && station.totalDurationSeconds > 0.0) {
            station.totalDurationSeconds
        } else {
            playableTracks.sumOf { it.durationSeconds }
        }

        val userAlreadyRequestedPlayback = player.playWhenReady
        setPlaylistAtLivePosition(prepare = userAlreadyRequestedPlayback)
        hasUserStartedPlayback = userAlreadyRequestedPlayback

        val skipped = station.tracks.size - playableTracks.size
        updateStatus(
            if (skipped == 0) "Ready — tap Play to join the live signal"
            else "Ready — skipped $skipped track(s) with missing media data"
        )
    }

    private fun setPlaylistAtLivePosition(prepare: Boolean) {
        if (playableTracks.isEmpty()) return

        val position = StationPositionCalculator.calculate(
            tracks = playableTracks,
            totalDurationSeconds = synchronizationDurationSeconds,
        )

        val mediaItems = playableTracks.mapIndexed { index, track -> track.toMediaItem(index) }
        player.setMediaItems(mediaItems, position.trackIndex, position.offsetMilliseconds)
        player.repeatMode = Player.REPEAT_MODE_ALL
        if (prepare) player.prepare()
    }

    private fun StationTrack.toMediaItem(index: Int): MediaItem =
        MediaItem.Builder()
            .setMediaId(id ?: "khjw-track-$index")
            .setUri(requireNotNull(audioUrl))
            .setMediaMetadata(
                MediaMetadata.Builder()
                    .setTitle(title)
                    .setArtist(artist)
                    .setAlbumTitle("Hoojshwah Radio")
                    .setArtworkUri(Uri.parse(STATION_ARTWORK_URL))
                    .build()
            )
            .build()

    private fun updateStatus(message: String) {
        if (!::mediaSession.isInitialized) return
        mediaSession.setSessionExtras(Bundle().apply { putString(EXTRA_STATUS, message) })
    }

    private val playerListener = object : Player.Listener {
        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            if (!playWhenReady || hasUserStartedPlayback) return

            if (playableTracks.isEmpty()) {
                updateStatus("Still loading KHJW — playback will begin when ready")
                return
            }

            // The first Play tap joins the current simulated-live position, not the
            // position from when the Activity or service happened to open.
            hasUserStartedPlayback = true
            setPlaylistAtLivePosition(prepare = true)
        }

        override fun onPlaybackStateChanged(playbackState: Int) {
            when (playbackState) {
                Player.STATE_BUFFERING -> updateStatus("Buffering the KHJW signal…")
                Player.STATE_READY -> {
                    errorSkipsRemaining = 1
                    updateStatus(if (player.isPlaying) "Playing native KHJW audio" else "KHJW is ready")
                }
                Player.STATE_ENDED -> updateStatus("The KHJW signal ended unexpectedly")
                else -> Unit
            }
        }

        override fun onIsPlayingChanged(isPlaying: Boolean) {
            if (isPlaying) updateStatus("Playing native KHJW audio")
            else if (player.playbackState == Player.STATE_READY) updateStatus("Paused")
        }

        override fun onPlayerError(error: PlaybackException) {
            if (errorSkipsRemaining > 0 && player.mediaItemCount > 1) {
                errorSkipsRemaining -= 1
                updateStatus("A track was unavailable; trying the next track…")
                player.seekToNextMediaItem()
                player.prepare()
            } else {
                updateStatus("Playback interrupted — check the connection and tap Play")
            }
        }
    }

    companion object {
        const val EXTRA_STATUS = "com.hoojshwah.khjw.STATUS"
        private const val STATION_ARTWORK_URL =
            "https://hoojshwah-radio-live.onrender.com/static/icons/hoojshwah-icon-512.png"
    }
}
