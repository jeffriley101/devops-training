import unittest

from app.main import station
from app.radio import build_album5_preview_tracks, load_album5_preview, load_station_config


EXPECTED_IDS = [f"album5-preview-{number:02d}" for number in range(1, 12)]


class Album5PreviewApiTest(unittest.TestCase):
    def test_preview_has_exact_order_and_is_separate_from_normal_playlist(self):
        response = station()
        preview_ids = [track["id"] for track in response["album5_preview_tracks"]]
        normal_ids = {track["id"] for track in response["tracks"]}

        self.assertEqual(EXPECTED_IDS, preview_ids)
        self.assertTrue(normal_ids.isdisjoint(EXPECTED_IDS))

    def test_preview_audio_urls_use_station_media_delivery(self):
        config = load_station_config()
        tracks = build_album5_preview_tracks(load_album5_preview(), config["media_base_url"])

        self.assertEqual(11, len(tracks))
        self.assertTrue(all(track["audio_url"].startswith(config["media_base_url"]) for track in tracks))
        self.assertEqual("audio/chasin-falls.mp3", tracks[8]["audio_path"])


if __name__ == "__main__":
    unittest.main()
