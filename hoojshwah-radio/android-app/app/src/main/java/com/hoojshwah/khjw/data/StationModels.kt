package com.hoojshwah.khjw.data

data class Station(
    val name: String,
    val totalDurationSeconds: Double,
    val tracks: List<StationTrack>,
    val album5PreviewTracks: List<StationTrack> = emptyList(),
)

data class StationTrack(
    val id: String?,
    val title: String,
    val artist: String,
    val audioUrl: String?,
    val durationSeconds: Double,
)
