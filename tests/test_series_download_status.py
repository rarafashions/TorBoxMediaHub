"""Download progress and auto-add state for the series panel."""
import time
import unittest
from unittest.mock import patch

import app as app_module


HASH_A = "a" * 40


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.content = repr(body).encode()
        self.text = ""

    def json(self):
        return self._body


class MonitorSnapshotTests(unittest.TestCase):
    def test_peek_keeps_completion_and_prunes_expired_entries(self):
        monitor = app_module.TorboxDownloadMonitor()
        now = time.time()
        monitor.watched_items["torrent:1"] = {"key": "torrent:1"}
        monitor.syncing_items = [{"key": "torrent:2"}]
        monitor.recently_imported = [
            {
                "key": "torrent:3",
                "series_title": "Dark",
                "season": 1,
                "episode": 4,
                "imported_at": now,
            },
            {
                "key": "torrent:4",
                "series_title": "Dark",
                "season": 1,
                "episode": 5,
                "imported_at": now - app_module.TORBOX_RECENT_IMPORT_TTL_SECONDS - 60,
            },
        ]

        first = monitor.status(consume_imported=False)
        second = monitor.status(consume_imported=False)
        self.assertEqual([e["key"] for e in first["newly_imported"]], ["torrent:3"])
        self.assertEqual([e["key"] for e in second["newly_imported"]], ["torrent:3"])
        self.assertEqual(first["active_count"], 2)

        consumed = monitor.status()
        self.assertEqual([e["key"] for e in consumed["newly_imported"]], ["torrent:3"])
        self.assertEqual(monitor.status()["newly_imported"], [])

    def test_track_keeps_episode_and_series_hint(self):
        monitor = app_module.TorboxDownloadMonitor()
        monitor._thread_started = True  # tracking only; never spawn the poller
        with patch.object(app_module.db, "save_torbox_import_hint"):
            monitor.track(
                "torrent",
                "101",
                "Dark.S01E04.1080p",
                series_title="Dark",
                category="tv_show",
                season=1,
                episode=4,
            )
        try:
            entry = monitor.status(consume_imported=False)["active"][0]
            self.assertEqual(entry["episode"], 4)
            self.assertEqual(entry["season"], 1)
            hint = app_module._TORBOX_IMPORT_HINTS["torrent:101"]["metadata"]["series"]
            self.assertEqual(hint, {"title": "Dark", "season": 1, "episode": 4})
        finally:
            app_module._TORBOX_IMPORT_HINTS.pop("torrent:101", None)


class ApplySeriesHintTests(unittest.TestCase):
    def test_complete_triple_pins_season_episode(self):
        payload = {"series_title": "Old"}
        result = app_module._apply_series_hint(
            payload, {"title": "Dark", "season": 2, "episode": 7}
        )
        self.assertEqual(result["series_title"], "Dark")
        self.assertEqual(result["season_num"], 2)
        self.assertEqual(result["episode_num"], 7)

    def test_partial_or_invalid_hint_is_ignored(self):
        for hint in (
            {},
            None,
            {"title": "Dark"},
            {"title": "Dark", "season": 1},
            {"title": "Dark", "season": 1, "episode": 0},
            {"title": "", "season": 1, "episode": 1},
            {"title": "Dark", "season": "x", "episode": 1},
        ):
            payload = {"file_name": "Dark.S01E01.mkv"}
            self.assertEqual(
                app_module._apply_series_hint(dict(payload), hint),
                payload,
            )


class SeriesDownloadStatusEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_series_title_is_required(self):
        response = self.client.get("/api/series/download-status")
        self.assertEqual(response.status_code, 400)

    def test_filters_series_and_season_without_consuming_events(self):
        snapshot = {
            "active_count": 3,
            "active": [
                {
                    "key": "torrent:1",
                    "title": "Dark.S01E01",
                    "series_title": "Dark",
                    "season": 1,
                    "episode": 1,
                    "progress": 40,
                },
                {
                    "key": "torrent:2",
                    "title": "Dark Matter.S01E01",
                    "series_title": "Dark Matter",
                    "season": 1,
                    "episode": 1,
                },
                {
                    "key": "torrent:3",
                    "title": "Dark.S02E01",
                    "series_title": "Dark",
                    "season": 2,
                    "episode": 1,
                },
            ],
            "syncing": [
                {
                    "key": "torrent:5",
                    "title": "Dark.S01E02",
                    "series_title": "Dark",
                    "season": 1,
                    "episode": 2,
                    "library_stage": "importing",
                }
            ],
            "newly_imported": [
                {
                    "key": "torrent:4",
                    "title": "Dark.S01E03",
                    "series_title": "Dark",
                    "season": 1,
                    "episode": 3,
                    "imported_at": 1.0,
                }
            ],
        }
        with patch.object(
            app_module.TORBOX_MONITOR, "status", return_value=snapshot
        ) as status:
            response = self.client.get(
                "/api/series/download-status?series_title=dark&season=1"
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual([e["key"] for e in body["active"]], ["torrent:1"])
        self.assertEqual([e["key"] for e in body["syncing"]], ["torrent:5"])
        self.assertEqual([e["key"] for e in body["newly_imported"]], ["torrent:4"])
        self.assertEqual(body["active_count"], 2)
        self.assertEqual(body["active"][0]["episode"], 1)
        self.assertNotIn("replace_media_id", body["active"][0])
        status.assert_called_once_with(consume_imported=False)


class AddCachedEpisodeTests(unittest.TestCase):
    def _post(self, payload):
        fake = FakeResponse(
            200, {"success": True, "data": {"torrent_id": 55}, "detail": "Added"}
        )
        with patch.object(
            app_module.db, "get_settings", return_value={"torbox_api_key": "k"}
        ), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post", return_value=fake
        ), patch.object(
            app_module.TORBOX_MONITOR, "track"
        ) as track:
            response = app_module.app.test_client().post(
                "/api/torbox/add-cached", json=payload
            )
        return response, track

    def test_episode_is_forwarded_to_the_monitor(self):
        response, track = self._post(
            {
                "kind": "torrent",
                "magnet": f"magnet:?xt=urn:btih:{HASH_A}",
                "title": "Dark.S01E03.1080p",
                "series_title": "Dark",
                "category": "tv_show",
                "season": 1,
                "episode": 3,
            }
        )
        self.assertEqual(response.status_code, 200)
        kwargs = track.call_args.kwargs
        self.assertEqual(kwargs["series_title"], "Dark")
        self.assertEqual(kwargs["season"], 1)
        self.assertEqual(kwargs["episode"], 3)

    def test_invalid_season_and_episode_fall_back(self):
        response, track = self._post(
            {
                "kind": "torrent",
                "magnet": f"magnet:?xt=urn:btih:{HASH_A}",
                "title": "Dark.S01",
                "series_title": "Dark",
                "category": "tv_show",
                "season": "abc",
                "episode": -2,
            }
        )
        self.assertEqual(response.status_code, 200)
        kwargs = track.call_args.kwargs
        self.assertEqual(kwargs["season"], 1)
        self.assertIsNone(kwargs["episode"])


if __name__ == "__main__":
    unittest.main()