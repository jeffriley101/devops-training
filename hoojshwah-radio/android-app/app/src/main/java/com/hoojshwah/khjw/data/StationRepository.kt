package com.hoojshwah.khjw.data

import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class StationRepository(
    private val stationUrl: String = STATION_API_URL,
    private val executor: ExecutorService = Executors.newSingleThreadExecutor(),
) {
    fun loadStation(callback: (Result<Station>) -> Unit) {
        executor.execute {
            callback(runCatching { fetchStation() })
        }
    }

    fun close() {
        executor.shutdownNow()
    }

    private fun fetchStation(): Station {
        val connection = (URL(stationUrl).openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 15_000
            requestMethod = "GET"
            setRequestProperty("Accept", "application/json")
            useCaches = false
        }

        try {
            val status = connection.responseCode
            if (status !in 200..299) {
                throw IOException("Station API returned HTTP $status")
            }

            val body = connection.inputStream.bufferedReader().use { it.readText() }
            return parseStation(body)
        } finally {
            connection.disconnect()
        }
    }

    internal fun parseStation(body: String): Station {
        try {
            val json = JSONObject(body)
            val tracksJson = json.optJSONArray("tracks")
                ?: throw StationDataException("Station response has no tracks array")

            val tracks = buildList {
                for (index in 0 until tracksJson.length()) {
                    val track = tracksJson.optJSONObject(index) ?: continue
                    add(
                        StationTrack(
                            id = track.optionalString("id"),
                            title = track.optionalString("title") ?: "Untitled KHJW track",
                            artist = track.optionalString("artist") ?: "Hoojshwah",
                            audioUrl = track.optionalString("audio_url"),
                            durationSeconds = track.optDouble("duration_seconds", 0.0),
                        )
                    )
                }
            }

            if (tracks.isEmpty()) {
                throw StationDataException("Station response contains zero tracks")
            }

            return Station(
                name = json.optionalString("station_name") ?: "Hoojshwah Radio",
                totalDurationSeconds = json.optDouble("total_duration_seconds", 0.0),
                tracks = tracks,
            )
        } catch (error: JSONException) {
            throw StationDataException("Station response is malformed", error)
        }
    }

    private fun JSONObject.optionalString(name: String): String? =
        optString(name, "").trim().takeIf { it.isNotEmpty() }

    companion object {
        const val STATION_API_URL = "https://hoojshwah-radio-live.onrender.com/api/station"
    }
}

class StationDataException(message: String, cause: Throwable? = null) : Exception(message, cause)

