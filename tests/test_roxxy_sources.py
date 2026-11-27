"""Five Roxxy addon slots, source categories, and automatic stream analysis."""
import os
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import roxxy_download_engine as engine
import roxxy_module as rm

TOKENS = {
    "jav": "j" * 40,
    "english_adult": "e" * 40,
    "onlyfans": "o" * 40,
    "indo_movie": "i" * 40,
    "donghua": "d" * 40,
}


def _manifest(source):
    return f"https://hub.roxxy-tech.com/{TOKENS[source]}/manifest.json"


def _settings():
    return {
        f"roxxy_manifest_url_{source}": _manifest(source)
        for source in rm.ROXXY_SOURCES
    }


class FiveManifestConfigTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_config_lists_five_slots_without_echoing_tokens(self):
        with patch.object(rm, "_settings", return_value=_settings()):
            response = self.client.get("/api/roxxy/config")

        body = response.get_data(as_text=True)
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(payload["manifests"]), set(rm.ROXXY_SOURCES))
        for token in TOKENS.values():
            self.assertNotIn(token, body)
        self.assertTrue(all(item["configured"] for item in payload["manifests"].values()))
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["extra_configured"])

    def test_saving_five_manifests_writes_each_slot(self):
        manifests = {source: _manifest(source) for source in rm.ROXXY_SOURCES}
        with patch.object(rm, "_save_private_settings") as save:
            response = self.client.post("/api/roxxy/config", json={
                "manifests": manifests,
                "download_path": r"E:\Movies",
            })

        self.assertEqual(response.status_code, 200)
        values = save.call_args.args[0]
        for source in rm.ROXXY_SOURCES:
            self.assertEqual(values[rm._MANIFEST_SETTINGS[source]], manifests[source])
        self.assertEqual(values["roxxy_manifest_url"], manifests["english_adult"])
        self.assertEqual(values["roxxy_extra_manifest_url"], manifests["donghua"])

    def test_one_invalid_slot_rejects_the_whole_save(self):
        with patch.object(rm, "_save_private_settings") as save:
            response = self.client.post("/api/roxxy/config", json={
                "manifests": {
                    "jav": _manifest("jav"),
                    "donghua": "http://evil.example/manifest.json",
                },
                "download_path": r"E:\Movies",
            })

        self.assertEqual(response.status_code, 400)
        save.assert_not_called()

    def test_legacy_selectors_still_map_to_english_adult_and_donghua(self):
        with patch.object(rm, "_settings", return_value=_settings()):
            self.assertEqual(rm._manifest_url("main"), _manifest("english_adult"))
            self.assertEqual(rm._manifest_url("extra"), _manifest("donghua"))
            self.assertEqual(rm._manifest_url("indo_movie"), _manifest("indo_movie"))


class FiveManifestMergeTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_manifest_merges_every_configured_slot(self):
        def fake_get(path, params=None, source="main"):
            return {
                "name": f"{source} addon",
                "types": [source],
                "catalogs": [{"type": source, "id": "1:latest", "name": "Latest"}],
            }

        with patch.object(rm, "_settings", return_value=_settings()), patch.object(
            rm, "_roxxy_get", side_effect=fake_get
        ):
            response = self.client.get("/api/roxxy/manifest")

        payload = response.get_json()
        self.assertEqual([c["source"] for c in payload["catalogs"]], list(rm.ROXXY_SOURCES))
        self.assertEqual([s["catalog_count"] for s in payload["sources"]], [1, 1, 1, 1, 1])

    def test_unconfigured_installation_returns_a_clear_error(self):
        with patch.object(rm, "_settings", return_value={}), patch.object(
            rm, "_roxxy_get"
        ) as fetch:
            response = self.client.get("/api/roxxy/manifest")

        self.assertEqual(response.status_code, 502)
        self.assertIn("Belum ada Manifest URL", response.get_json()["error"])
        fetch.assert_not_called()


class SourceCategoryTests(unittest.TestCase):
    def _identify(self, source, title):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "download.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            return engine._identify_roxxy_media(
                {
                    "file_path": path,
                    "file_size": 1,
                    "metadata": {"name": title, "_roxxySource": source},
                },
                {"size": 1},
            )

    def test_indonesia_movies_always_uses_its_own_category(self):
        result = self._identify("indo_movie", "Sebuah Film Indonesia 2024")
        self.assertEqual(result["category"], "indo_movie")
        self.assertEqual(result["status"], "identified")
        self.assertEqual(
            result["validation"]["d2_identification"]["subtitle_policy"], "roxxy_only"
        )

    def test_onlyfans_lands_in_english_adult(self):
        result = self._identify("onlyfans", "Some Creator Scene")
        self.assertEqual(result["category"], "english_adult")

    def test_register_marks_indonesia_movies_as_non_adult(self):
        captured = {}
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "film.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            task = {
                "file_path": path,
                "file_size": 1,
                "metadata": {"name": "Film Indonesia", "_roxxySource": "indo_movie"},
                "validation": {
                    "size": 1,
                    "d2_identification": {
                        "category": "indo_movie",
                        "classification_status": "classified",
                    },
                },
            }
            with patch.object(
                engine.db, "upsert_media_item",
                side_effect=lambda data: captured.update(data) or 55,
            ), patch.object(engine, "cache_library_poster"):
                result = engine._register_roxxy_media(task)

        self.assertEqual(result["media_id"], 55)
        self.assertEqual(captured["content_category"], "indo_movie")
        self.assertEqual(captured["is_adult"], 0)


class AnalyzeOptionsTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def _analyze(self, stream):
        return self.client.post("/api/roxxy/analyze", json={"stream": stream}).get_json()["analysis"]

    def test_magnet_exposes_cached_and_uncached_torbox_routes(self):
        analysis = self._analyze({"url": "magnet:?xt=urn:btih:" + "a" * 40})
        modes = [(o["kind"], o["mode"]) for o in analysis["options"]]
        self.assertEqual(modes, [("torrent", "cached"), ("torrent", "uncached")])

    def test_nzb_goes_to_torbox_usenet(self):
        analysis = self._analyze({"url": "https://hoster.example/release.nzb"})
        self.assertEqual(analysis["route"], "torbox_usenet")
        self.assertEqual(analysis["nzb_url"], "https://hoster.example/release.nzb")
        self.assertEqual([o["kind"] for o in analysis["options"]], ["usenet"])

    def test_http_media_exposes_both_local_engines(self):
        analysis = self._analyze({"url": "https://cdn.example/movie.m3u8"})
        engines = [o["engine"] for o in analysis["options"]]
        self.assertEqual(engines, ["yt-dlp", "aria2c"])
        recommended = [o["engine"] for o in analysis["options"] if o.get("recommended")]
        self.assertEqual(recommended, ["yt-dlp"])


class IndonesiaMovieGuardTests(unittest.TestCase):
    def test_reclassification_cannot_demote_indonesia_movies(self):
        result = app_module._guard_adult_reclassification(
            {"content_category": "indo_movie"},
            {"classification_status": "classified", "detected_category": "general_movie"},
        )
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertIsNone(result["detected_category"])


if __name__ == "__main__":
    unittest.main()