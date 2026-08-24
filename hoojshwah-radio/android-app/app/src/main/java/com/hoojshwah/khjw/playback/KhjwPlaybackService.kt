package com.hoojshwah.khjw.playback

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.annotation.OptIn
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommand
import androidx.media3.session.SessionError
import androidx.media3.session.SessionResult
import com.google.common.util.concurrent.Futures
import com.google.common.util.concurrent.ListenableFuture
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
    private var errorSkipsRemaining = 1
    private var backstageSelectionActive = false

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

        val radioSessionPlayer = object : ForwardingPlayer(player) {
            override fun play() {
                handlePlayRequest()
            }

            override fun pause() {
                handlePauseRequest()
            }

            override fun setPlayWhenReady(playWhenReady: Boolean) {
                if (playWhenReady) handlePlayRequest() else handlePauseRequest()
            }
        }

        mediaSession = MediaSession.Builder(this, radioSessionPlayer)
            .setCallback(sessionCallback)
            .build()

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
        backstageSelectionActive = false
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

        val skipped = station.tracks.size - playableTracks.size
        updateStatus(
            if (userAlreadyRequestedPlayback) "Joining the live KHJW signal…"
            else if (skipped == 0) "Ready — tap Play to join the live signal"
            else "Ready — skipped $skipped track(s) with missing media data"
        )
    }

    private fun setPlaylistAtLivePosition(prepare: Boolean) {
        if (playableTracks.isEmpty()) return

        val position = StationPositionCalculator.calculate(
            tracks = playableTracks,
            totalDurationSeconds = synchronizationDurationSeconds,
        )
        val selectedTrack = playableTracks[position.trackIndex]

        Log.i(
            TAG,
            "Live position: index=${position.trackIndex}, " +
                "track=${selectedTrack.id ?: selectedTrack.title}, " +
                "offsetMs=${position.offsetMilliseconds}",
        )

        val mediaItems = playableTracks.mapIndexed { index, track -> track.toMediaItem(index) }
        player.setMediaItems(mediaItems, position.trackIndex, position.offsetMilliseconds)
        player.repeatMode = Player.REPEAT_MODE_ALL
        if (prepare) {
            player.prepare()
            logPlaybackParameters("live join")
        }
    }

    private fun handlePlayRequest() {
        if (player.isPlaying) {
            Log.i(TAG, "Play received while already playing; no-op")
            return
        }

        Log.i(TAG, "Play received; rejoining live signal")
        if (playableTracks.isEmpty()) {
            // Preserve the play intent. configureStation() will calculate a fresh live
            // position and prepare playback once the station data arrives.
            player.play()
            Log.i(TAG, "Play is waiting for station data")
            updateStatus("Still loading KHJW — playback will begin when ready")
            return
        }

        backstageSelectionActive = false

        // Establish the current live position before allowing audible playback, including
        // when ExoPlayer is idle/stopped but playWhenReady was already true.
        setPlaylistAtLivePosition(prepare = true)
        player.play()
    }

    private fun handlePauseRequest() {
        Log.i(TAG, "Pause received")
        player.pause()
    }

    private fun playBackstageTrack(trackId: String): Boolean {
        val requestedIndex = playableTracks.indexOfFirst { track -> track.id == trackId }
        if (requestedIndex < 0) return false

        backstageSelectionActive = true
        player.seekToDefaultPosition(requestedIndex)
        player.prepare()
        player.play()
        updateStatus("Playing a Hoojshwah playlist pick")
        return true
    }

    private fun logPlaybackParameters(event: String) {
        val parameters = player.playbackParameters
        Log.i(TAG, "Playback parameters at $event: speed=${parameters.speed}, pitch=${parameters.pitch}")
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

    private val sessionCallback = object : MediaSession.Callback {
        override fun onConnectAsync(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
        ): ListenableFuture<MediaSession.ConnectionResult> {
            val resultBuilder = MediaSession.ConnectionResult.AcceptedResultBuilder(session, controller)
                .setAvailablePlayerCommands(RADIO_PLAYER_COMMANDS)

            if (controller.packageName == packageName && controller.uid == applicationInfo.uid) {
                resultBuilder.setAvailableSessionCommands(
                    MediaSession.ConnectionResult.DEFAULT_SESSION_COMMANDS.buildUpon()
                        .add(NativePlaybackCommands.selectBackstageTrack)
                        .build()
                )
            }

            return Futures.immediateFuture(resultBuilder.build())
        }

        override fun onCustomCommand(
            session: MediaSession,
            controller: MediaSession.ControllerInfo,
            customCommand: SessionCommand,
            args: Bundle,
        ): ListenableFuture<SessionResult> {
            if (
                controller.packageName != packageName ||
                controller.uid != applicationInfo.uid ||
                customCommand != NativePlaybackCommands.selectBackstageTrack
            ) {
                return Futures.immediateFuture(SessionResult(SessionError.ERROR_PERMISSION_DENIED))
            }

            val trackId = args.getString(NativePlaybackCommands.ARG_TRACK_ID)
            val resultCode = if (trackId != null && playBackstageTrack(trackId)) {
                SessionResult.RESULT_SUCCESS
            } else {
                SessionError.ERROR_BAD_VALUE
            }
            return Futures.immediateFuture(SessionResult(resultCode))
        }
    }

    private val playerListener = object : Player.Listener {
        override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
            Log.i(TAG, "PlayWhenReady changed: playWhenReady=$playWhenReady, reason=$reason")
        }

        override fun onMediaItemTransition(mediaItem: MediaItem?, reason: Int) {
            val parameters = player.playbackParameters
            Log.i(
                TAG,
                "Media item transition: reason=$reason, index=${player.currentMediaItemIndex}, " +
                    "track=${mediaItem?.mediaId ?: "unknown"}, speed=${parameters.speed}, " +
                "pitch=${parameters.pitch}",
            )

            if (backstageSelectionActive && reason == Player.MEDIA_ITEM_TRANSITION_REASON_AUTO) {
                backstageSelectionActive = false
                mainHandler.post {
                    setPlaylistAtLivePosition(prepare = true)
                    player.play()
                }
            }
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
            Log.e(TAG, "Player error: ${error.errorCodeName}", error)
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
        private const val TAG = "KHJWPlayback"
        private const val STATION_ARTWORK_URL =
            "https://hoojshwah-radio-live.onrender.com/static/icons/hoojshwah-icon-512.png"

        private val RADIO_PLAYER_COMMANDS = Player.Commands.Builder()
            .addAll(
                Player.COMMAND_PLAY_PAUSE,
                Player.COMMAND_GET_CURRENT_MEDIA_ITEM,
                Player.COMMAND_GET_METADATA,
                Player.COMMAND_GET_AUDIO_ATTRIBUTES,
                Player.COMMAND_GET_VOLUME,
                Player.COMMAND_GET_DEVICE_VOLUME,
            )
            .build()
    }
}
