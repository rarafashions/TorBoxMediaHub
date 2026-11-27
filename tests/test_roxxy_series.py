"""Season/episode parsing, per-episode Library flags, and multi-addon routing."""
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import roxxy_download_engine as engine
import roxxy_module as rm

SERIES_NAME = "Battle Through the Heavens Season 5"
TOKEN = "a" * 40
MAIN_MANIFEST = f"https://hub.roxxy-tech.com/{TOKEN}/manifest.json"
EXTRA_MANIFEST = f"https://hub.roxxy-tech.com/{'b' * 40}/manifest.json"


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
        "series_title": None,
        "code_id": None,
    }
    base.update(overrides)
    return base


def _episode(number, title=None, season=1):
    return {
        "id": (
            "dhs:anime|battle-through-the-heavens-season-5th:"
            f"battle-through-the-heavens-season-5th-episode-{number}-multiple-subtitles"
        ),
        "title": title or f"{SERIES_NAME} Episode {number} Multiple Subtitles",
        "season": season,
        "episode": number,
    }


class SeriesIndexTests(unittest.TestCase):
    def test_groups_ascending_and_defaults_to_the_newest_episode(self):
        videos = [_episode(48), _episode(219), _episode(47), _episode(46)]
        series = rm._build_series_index(videos, SERIES_NAME)

        self.assertEqual(series["episode_count"], 4)
        self.assertFalse(series["multi_season"])
        episodes = series["seasons"][0]["episodes"]
        self.assertEqual([e["episode"] for e in episodes], [46, 47, 48, 219])
        self.assertEqual(episodes[-1]["id"], series["default_episode_id"])
        self.assertEqual(episodes[0]["label"], "E46")
        self.assertEqual(episodes[0]["display_title"], "Multiple Subtitles")

    def test_multiple_seasons_add_season_code_to_every_label(self):
        videos = [_episode(1, season=2), _episode(2, season=1), _episode(1, season=1)]
        series = rm._build_series_index(videos, SERIES_NAME)

        self.assertTrue(series["multi_season"])
        self.assertEqual(series["season_count"], 2)
        labels = [e["label"] for group in series["seasons"] for e in group["episodes"]]
        self.assertEqual(labels, ["S01·E1", "S01·E2", "S02·E1"])
        # Newest = last season, last episode.
        self.assertEqual(series["default_episode_id"], _episode(1, season=2)["id"])

    def test_inconsistent_episode_title_still_uses_the_episode_number(self):
        videos = [_episode(138, title="Episode 137"), _episode(139)]
        series = rm._build_series_index(videos, SERIES_NAME)

        episodes = series["seasons"][0]["episodes"]
        self.assertEqual([e["episode"] for e in episodes], [138, 139])
        self.assertEqual(episodes[0]["label"], "E138")

    def test_empty_or_invalid_videos_yield_none(self):
        self.assertIsNone(rm._build_series_index([], SERIES_NAME))
        self.assertIsNone(rm._build_series_index([{"title": "no id"}], SERIES_NAME))


class EpisodeLibraryMatchTests(unittest.TestCase):
    def setUp(self):
        self.index = rm._build_library_index([
            _row(
                id=900,
                title="Battle Through the Heavens S05E219 Multiple Subtitles",
                file_name="ep219.mkv",
                series_title=SERIES_NAME,
                season_num=1,
                episode_num=219,
                content_category="tv_show",
            )
        ])

    def test_series_season_episode_key_matches(self):
        match = rm._episode_library_match(SERIES_NAME, 1, 219, "Other title", self.index)
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 900)
        self.assertEqual(match["match_reason"], "series_episode")

    def test_series_title_alone_still_does_not_mark_every_episode(self):
        self.assertIsNone(
            rm._library_match_for_meta({"id": "series:1", "name": SERIES_NAME}, self.index)
        )

    def test_other_episode_of_the_same_series_is_not_owned(self):
        self.assertIsNone(
            rm._episode_library_match(SERIES_NAME, 1, 46, "Episode 46", self.index)
        )


class SeriesMetaEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.meta = {
            "id": "dhs:anime|battle-through-the-heavens-season-5th",
            "type": "series",
            "name": SERIES_NAME,
            "videos": [_episode(48), _episode(219), _episode(46)],
        }
        self.index = rm._build_library_index([
            _row(
                id=900,
                title="Battle Through the Heavens S05E219",
                series_title=SERIES_NAME,
                season_num=1,
                episode_num=219,
            )
        ])

    def test_meta_exposes_grouped_episodes_with_library_flags(self):
        with patch.object(rm, "_roxxy_get", return_value={"meta": self.meta}), patch.object(
            rm, "_load_library_index", return_value=self.index
        ):
            response = self.client.get(
                "/api/roxxy/meta/Donghuastream/dhs%3Aanime%7Cbattle-through-the-heavens-season-5th"
            )

        self.assertEqual(response.status_code, 200)
        meta = response.get_json()["meta"]
        self.assertTrue(meta["is_series"])
        self.assertEqual(meta["series"]["default_episode_id"], _episode(219)["id"])
        flags = {
            episode["episode"]: episode["in_library"]
            for episode in meta["series"]["seasons"][0]["episodes"]
        }
        self.assertEqual(flags, {46: False, 48: False, 219: True})
        self.assertEqual(meta["series"]["library_count"], 1)

    def test_movie_meta_has_no_series_block(self):
        with patch.object(
            rm, "_roxxy_get", return_value={"meta": {"id": "pornhub:1", "name": "Scene"}}
        ), patch.object(rm, "_load_library_index", return_value={}):
            response = self.client.get("/api/roxxy/meta/movie/pornhub:1")

        meta = response.get_json()["meta"]
        self.assertNotIn("series", meta)
        self.assertFalse(meta.get("is_series"))


class MultiAddonRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_requests_forward_the_selected_source(self):
        paths = [
            "/api/roxxy/catalog/Pornhub/427298:newest",
            "/api/roxxy/meta/Donghuastream/dhs%3Aanime%7Cseries",
            "/api/roxxy/streams/Donghuastream/dhs%3Aanime%7Cseries%3Aep1",
        ]
        for path in paths:
            with self.subTest(path=path), patch.object(
                rm, "_roxxy_get", return_value={"metas": [], "meta": {}, "streams": []}
            ) as fetch, patch.object(rm, "_load_library_index", return_value={}):
                response = self.client.get(path + "?source=extra")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(fetch.call_args.kwargs["source"], "extra")

    def test_unknown_source_falls_back_to_main(self):
        with patch.object(rm, "_roxxy_get", return_value={"metas": []}) as fetch, patch.object(
            rm, "_load_library_index", return_value={}
        ):
            self.client.get("/api/roxxy/catalog/Pornhub/1:newest?source=evil")
        self.assertEqual(fetch.call_args.kwargs["source"], "main")

    def test_manifest_merges_catalogs_from_both_addons(self):
        settings = {
            "roxxy_manifest_url": MAIN_MANIFEST,
            "roxxy_extra_manifest_url": EXTRA_MANIFEST,
        }
        manifests = {
            "main": {"name": "Video Feeds", "types": ["Pornhub"], "catalogs": [
                {"type": "Pornhub", "id": "1:newest", "name": "Newest"}
            ]},
            "extra": {"name": "Donghua", "types": ["Donghuastream"], "catalogs": [
                {"type": "Donghuastream", "id": "427357:latest", "name": "Latest"}
            ]},
        }

        def fake_get(path, params=None, source="main"):
            return manifests[source]

        with patch.object(rm, "_settings", return_value=settings), patch.object(
            rm, "_roxxy_get", side_effect=fake_get
        ):
            response = self.client.get("/api/roxxy/manifest")

        payload = response.get_json()
        self.assertEqual([c["source"] for c in payload["catalogs"]], ["main", "extra"])
        self.assertEqual(payload["types"], ["Pornhub", "Donghuastream"])

    def test_extra_manifest_failure_keeps_the_main_catalog(self):
        settings = {
            "roxxy_manifest_url": MAIN_MANIFEST,
            "roxxy_extra_manifest_url": EXTRA_MANIFEST,
        }

        def fake_get(path, params=None, source="main"):
            if source == "extra":
                raise ValueError("addon kedua mati")
            return {"types": ["Pornhub"], "catalogs": [{"type": "Pornhub", "id": "1", "name": "A"}]}

        with patch.object(rm, "_settings", return_value=settings), patch.object(
            rm, "_roxxy_get", side_effect=fake_get
        ):
            response = self.client.get("/api/roxxy/manifest")

        payload = response.get_json()
        self.assertEqual(len(payload["catalogs"]), 1)
        self.assertTrue(payload["extra_error"])

    def test_stream_refresh_uses_the_episode_addon(self):
        payload = {"streams": [{"url": "https://cdn.example/a/file.m3u8?sig=new"}]}
        with patch.object(rm, "_roxxy_get", return_value=payload) as fetch:
            engine._refresh_stream_url(
                {"id": "ep1", "_roxxyType": "Donghuastream", "_roxxySource": "extra"},
                {"url": "https://cdn.example/a/file.m3u8?sig=old"},
            )
        self.assertEqual(fetch.call_args.kwargs["source"], "extra")


class ManifestConfigTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_config_never_echoes_tokens(self):
        settings = {
            "roxxy_manifest_url": MAIN_MANIFEST,
            "roxxy_extra_manifest_url": EXTRA_MANIFEST,
        }
        with patch.object(rm, "_settings", return_value=settings):
            response = self.client.get("/api/roxxy/config")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(TOKEN, body)
        self.assertNotIn("b" * 40, body)
        self.assertTrue(response.get_json()["extra_configured"])

    def test_saving_extra_manifest_keeps_the_main_token(self):
        with patch.object(rm, "_save_private_settings") as save:
            response = self.client.post("/api/roxxy/config", json={
                "extra_manifest_url": EXTRA_MANIFEST,
                "download_path": r"E:\Movies",
            })

        self.assertEqual(response.status_code, 200)
        values = save.call_args.args[0]
        self.assertEqual(values["roxxy_extra_manifest_url"], EXTRA_MANIFEST)
        self.assertNotIn("roxxy_manifest_url", values)

    def test_invalid_extra_manifest_is_rejected_without_saving(self):
        with patch.object(rm, "_save_private_settings") as save:
            response = self.client.post("/api/roxxy/config", json={
                "extra_manifest_url": "http://evil.example/manifest.json",
                "download_path": r"E:\Movies",
            })

        self.assertEqual(response.status_code, 400)
        save.assert_not_called()


class EpisodeRegistrationTests(unittest.TestCase):
    def test_registration_keeps_series_season_episode_fields(self):
        captured = {}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "episode-219.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            task = {
                "file_path": path,
                "file_size": 1,
                "metadata": {
                    "name": f"{SERIES_NAME} - E219 - Multiple Subtitles",
                    "series_title": SERIES_NAME,
                    "season_num": 1,
                    "episode_num": 219,
                },
                "validation": {
                    "size": 1,
                    "d2_identification": {
                        "category": "general",
                        "classification_status": "classified",
                    },
                },
            }
            with patch.object(
                engine.db, "upsert_media_item", side_effect=lambda data: captured.update(data) or 77
            ), patch.object(engine, "cache_library_poster"):
                result = engine._register_roxxy_media(task)

        self.assertEqual(result["media_id"], 77)
        self.assertEqual(captured["season_num"], 1)
        self.assertEqual(captured["episode_num"], 219)
        self.assertEqual(captured["series_title"], SERIES_NAME)
        self.assertIn("E219", captured["title"])


if __name__ == "__main__":
    unittest.main()