"""Secret surfaces: manifest tokens and signed stream URLs never leave the API.

`/api/settings` masks API keys and manifest URLs, while playlist payloads sent
to the browser omit the signed ``stream_url`` that only the server needs for
playback and M3U generation.
"""

import json
import unittest
from unittest.mock import patch

import app as tbh
import database_manager as db

TOKEN = "TOKENabcdefghijklmnopqrstuvwx"
MANIFEST = f"https://hub.example.test/{TOKEN}/manifest.json"
SIGNED = "https://cdn.example.test/movie.mkv?token=SIGNEDSECRET1234567890"

PLAYLIST = {
    "id": 1,
    "name": "Uji",
    "file_name": "uji.m3u",
    "items": [
        {"id": 9, "media_id": 3, "title": "Contoh", "file_name": "contoh.mkv", "stream_url": SIGNED},
    ],
}


class ManifestMaskingTests(unittest.TestCase):
    def test_mask_keeps_host_and_file_name(self):
        masked = db._mask_manifest_url(MANIFEST)
        self.assertNotIn(TOKEN, masked)
        self.assertIn("hub.example.test", masked)
        self.assertTrue(masked.endswith("/manifest.json"))
        self.assertIn("…", masked)

    def test_mask_handles_empty_and_relative_values(self):
        self.assertEqual(db._mask_manifest_url(""), "")
        self.assertEqual(db._mask_manifest_url("./manifest.json"), "••••manifest")

    def test_masked_settings_hides_manifest_token(self):
        raw = {
            "roxxy_manifest_url": MANIFEST,
            "roxxy_manifest_url_jav": MANIFEST,
            "torbox_api_key": "K" * 24,
        }
        with patch.object(db, "get_settings", return_value=raw):
            masked = db.get_masked_settings()
        blob = json.dumps(masked)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn("K" * 24, blob)
        self.assertTrue(masked["has_torbox_api_key"])
        self.assertIn("hub.example.test", masked["roxxy_manifest_url"])

    def test_settings_endpoint_never_returns_manifest_token(self):
        client = tbh.app.test_client()
        with patch.object(db, "get_settings", return_value={"roxxy_manifest_url": MANIFEST}), \
                patch.object(db, "get_subtitle_preferences", return_value={}), \
                patch.object(db, "get_subtitle_language_stats", return_value={}), \
                patch.object(db, "list_db_backups", return_value=[]), \
                patch.object(db, "get_default_player_paths", return_value={}), \
                patch.object(tbh, "detect_installed_players", return_value=[]), \
                patch.object(tbh, "_get_environment_info", return_value={}):
            body = client.get("/api/settings").get_data(as_text=True)
        self.assertNotIn(TOKEN, body)
        self.assertIn("hub.example.test", body)


class PlaylistPayloadTests(unittest.TestCase):
    def test_bootstrap_omits_signed_stream_url(self):
        client = tbh.app.test_client()
        media = [{"id": 3, "title": "Contoh", "stream_url": SIGNED, "playlist_names": []}]
        with patch.object(db, "verify_subtitle_files"), \
                patch.object(db, "query_media_items", return_value=media), \
                patch.object(db, "get_telemetry_stats", return_value={}), \
                patch.object(db, "get_all_playlists", return_value=[PLAYLIST]), \
                patch.object(tbh, "_get_environment_info", return_value={}):
            body = client.get("/api/bootstrap").get_json()
        item = body["playlists"][0]["items"][0]
        self.assertEqual(item["media_id"], 3)
        self.assertNotIn("stream_url", item)
        self.assertEqual(body["items"][0]["title"], "Contoh")
        self.assertNotIn("stream_url", body["items"][0])
        self.assertNotIn("SIGNEDSECRET", json.dumps(body))

    def test_media_list_omits_signed_stream_url(self):
        client = tbh.app.test_client()
        media = [{"id": 3, "title": "Contoh", "stream_url": SIGNED, "subtitle_tracks": []}]
        with patch.object(db, "query_media_items", return_value=media), \
                patch.object(db, "get_telemetry_stats", return_value={}):
            body = client.get("/api/media").get_json()
        self.assertNotIn("stream_url", body["items"][0])
        self.assertNotIn("SIGNEDSECRET", json.dumps(body))

    def test_playlists_endpoint_omits_signed_stream_url(self):
        client = tbh.app.test_client()
        with patch.object(db, "get_all_playlists", return_value=[PLAYLIST]), \
                patch.object(db, "get_telemetry_stats", return_value={}):
            body = client.get("/api/playlists").get_json()
        item = body["playlists"][0]["items"][0]
        self.assertNotIn("stream_url", item)
        self.assertNotIn("SIGNEDSECRET", json.dumps(body))


if __name__ == "__main__":
    unittest.main()