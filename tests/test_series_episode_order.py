"""Duplicate episodes stay visible but the best copy is listed first."""
import unittest
from unittest.mock import patch

import app as app_module


def _row(media_id, file_name, episode=2, size=1):
    return {
        "id": media_id,
        "series_title": "Dark",
        "content_category": "tv_show",
        "season_num": 1,
        "episode_num": episode,
        "file_name": file_name,
        "file_size": size,
        "year": 2017,
        "title": f"Dark E{episode}",
        "has_subtitle": 0,
    }


class ReleaseQualityScoreTests(unittest.TestCase):
    def test_resolution_tokens_rank_from_high_to_low(self):
        for name, expected in (
            ("Show.S01E02.2160p.mkv", 2160),
            ("Show.S01E02.4K.HDR.mkv", 2160),
            ("Show.S01E02.1080p.mkv", 1080),
            ("Show.S01E02.720p.mkv", 720),
            ("Show.S01E02.480p.mkv", 480),
            ("Show.S01E02.mkv", 0),
            ("", 0),
        ):
            self.assertEqual(app_module._release_quality_score(name), expected, msg=name)

    def test_ambiguous_numbers_are_not_mistaken_for_resolution(self):
        self.assertEqual(app_module._release_quality_score("Dark S01E02 2160.mkv"), 0)


class EpisodeOrderingEndpointTests(unittest.TestCase):
    def _response(self, rows):
        cached = {"seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 2}],
                  "external_id": None}
        with patch.object(app_module.db, "query_media_items", return_value=rows), \
                patch.object(app_module.db, "get_cached_series_seasons", return_value=cached), \
                patch.object(app_module, "_attach_subtitle_track_summaries"):
            client = app_module.app.test_client()
            return client.get(
                "/api/series/episodes-status?series_title=Dark&category=tv_show&season=1"
            )

    def test_best_quality_copy_of_the_same_episode_comes_first(self):
        rows = [
            _row(2, "Dark.S01E02.720p.mkv", size=500),
            _row(1, "Dark.S01E01.1080p.mkv", episode=1, size=900),
            _row(3, "Dark.S01E02.1080p.mkv", size=1500),
            _row(4, "Dark.S01E02.DVDRip.mkv", size=4000),
        ]
        payload = self._response(rows).get_json()
        self.assertTrue(payload["ok"])
        # Same episode: 1080p before 720p; an unknown resolution ranks after known ones.
        self.assertEqual([it["id"] for it in payload["items"]], [1, 3, 2, 4])

    def test_equal_quality_falls_back_to_file_size(self):
        rows = [
            _row(5, "Dark.S01E02.1080p.WEB.mkv", size=800),
            _row(6, "Dark.S01E02.1080p.BluRay.mkv", size=2000),
        ]
        payload = self._response(rows).get_json()
        self.assertEqual([it["id"] for it in payload["items"]], [6, 5])


if __name__ == "__main__":
    unittest.main()