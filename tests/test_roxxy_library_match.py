"""Library-presence matching for the Video Feeds (Roxxy) catalog and detail view."""
import unittest
from unittest.mock import patch

import app as app_module
import roxxy_module as rm


def _row(**overrides):
    base = {
        "id": 1,
        "title": "",
        "file_name": "",
        "parent_name": "",
        "source_type": "cloud",
        "content_category": "uncategorized",
        "has_metadata": 1,
        "has_subtitle": 0,
        "season_num": None,
        "episode_num": None,
        "code_id": None,
    }
    base.update(overrides)
    return base


class LibraryMatchKeyTests(unittest.TestCase):
    def test_folds_case_punctuation_and_extension(self):
        self.assertEqual(
            rm._library_match_key("Massage.Gone.Squirt.mp4"),
            "massage gone squirt",
        )
        self.assertEqual(
            rm._library_match_key("  Massage  Gone Squirt  "),
            "massage gone squirt",
        )

    def test_rejects_short_and_numeric_keys(self):
        for value in ("", "It", "5", "2024", "Binor"):
            with self.subTest(value=value):
                self.assertEqual(rm._library_match_key(value), "")


class LibraryIndexTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row(
                id=534,
                title="Massage Gone Squirt",
                file_name="Massage Gone Squirt.mp4",
                parent_name="Massage Gone Squirt",
                content_category="indo",
            ),
            _row(
                id=155,
                title="NGHJ-084 - Platonic friends",
                file_name="NGHJ-084.mp4",
                code_id="NGHJ-084",
                content_category="jav",
            ),
            _row(
                id=72,
                title="Irrepressible",
                file_name="Jessica Ryan.mp4",
                content_category="english_adult",
            ),
            _row(
                id=4,
                title="House of the Dragon",
                file_name="hotd.s01e04.mkv",
                season_num=1,
                episode_num=4,
                content_category="tv_show",
            ),
        ]
        self.index = rm._build_library_index(self.rows)

    def test_title_match_covers_code_less_meta(self):
        match = rm._library_match_for_meta(
            {"id": "pornhub:abc123", "name": "Massage Gone Squirt"}, self.index
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 534)
        self.assertEqual(match["match_reason"], "title")

    def test_file_name_match_when_meta_name_is_punctuated(self):
        match = rm._library_match_for_meta(
            {"id": "pornhub:def456", "name": "Massage.Gone.Squirt"}, self.index
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 534)

    def test_code_match_wins_before_title(self):
        match = rm._library_match_for_meta(
            {"id": "2jav:ngHJ-084", "name": "Different title", "jav_code": "nghj-084"},
            self.index,
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 155)
        self.assertEqual(match["match_reason"], "code")

    def test_episodic_rows_are_ignored(self):
        # A series title in the library must not advertise every episode as owned.
        self.assertIsNone(
            rm._library_match_for_meta(
                {"id": "series:hotd", "name": "House of the Dragon"}, self.index
            )
        )

    def test_unknown_title_is_not_a_match(self):
        self.assertIsNone(
            rm._library_match_for_meta(
                {"id": "pornhub:zzz", "name": "Completely Different Scene"}, self.index
            )
        )


class LibraryBadgeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        rows = [
            _row(
                id=534,
                title="Massage Gone Squirt",
                file_name="Massage Gone Squirt.mp4",
                content_category="indo",
            )
        ]
        self.index = rm._build_library_index(rows)

    def test_meta_marks_in_library_without_jav_code(self):
        meta = {"id": "pornhub:abc123", "name": "Massage Gone Squirt", "type": "movie"}
        with patch.object(rm, "_roxxy_get", return_value={"meta": meta}), patch.object(
            rm, "_load_library_index", return_value=self.index
        ):
            response = self.client.get("/api/roxxy/meta/movie/pornhub:abc123")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["meta"]
        self.assertTrue(payload["in_library"])
        self.assertEqual(payload["library_match"]["id"], 534)

    def test_meta_stays_not_in_library_for_unknown_title(self):
        meta = {"id": "pornhub:zzz", "name": "Unknown Scene", "type": "movie"}
        with patch.object(rm, "_roxxy_get", return_value={"meta": meta}), patch.object(
            rm, "_load_library_index", return_value=self.index
        ):
            response = self.client.get("/api/roxxy/meta/movie/pornhub:zzz")
        payload = response.get_json()["meta"]
        self.assertFalse(payload["in_library"])
        self.assertIsNone(payload["library_match"])

    def test_catalog_annotates_every_card(self):
        body = {
            "metas": [
                {"id": "pornhub:abc123", "name": "Massage Gone Squirt"},
                {"id": "pornhub:zzz", "name": "Unknown Scene"},
            ],
            "cacheMaxAge": 0,
        }
        with patch.object(rm, "_roxxy_get", return_value=body), patch.object(
            rm, "_load_library_index", return_value=self.index
        ):
            response = self.client.get("/api/roxxy/catalog/movie/pornhub")
        metas = response.get_json()["metas"]
        self.assertTrue(metas[0]["in_library"])
        self.assertFalse(metas[1]["in_library"])


if __name__ == "__main__":
    unittest.main()