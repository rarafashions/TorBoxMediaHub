"""Five Roxxy addon slots, source categories, and automatic stream analysis."""
import base64
import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
import database_manager as db
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
            ), patch.object(engine, "cache_library_poster"), patch.object(
                engine.db, "save_classification_result", return_value=None
            ) as persist:
                result = engine._register_roxxy_media(task)

        self.assertEqual(result["media_id"], 55)
        self.assertEqual(captured["content_category"], "indo_movie")
        self.assertEqual(captured["is_adult"], 0)
        # D3 owns the row, so the verdict must not stay "pending" for a later batch.
        persist.assert_called_once()
        self.assertEqual(persist.call_args.args[0], 55)
        verdict = persist.call_args.args[1]
        self.assertEqual(verdict["classification_status"], "classified")
        self.assertEqual(verdict["detected_category"], "indo_movie")
        self.assertEqual(persist.call_args.kwargs["changed_by"], "roxxy_d3")


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


class ConfigSaveMessageTests(unittest.TestCase):
    """Saving config must say what actually changed, not just echo a count."""

    def setUp(self):
        self.client = app_module.app.test_client()

    def _post(self, payload, settings):
        with patch.object(rm, "_settings", return_value=settings), \
                patch.object(rm, "_save_private_settings"):
            response = self.client.post("/api/roxxy/config", json=payload)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_folder_only_change_does_not_claim_a_manifest_update(self):
        settings = {**_settings(), "roxxy_download_path": r"E:\Old"}
        payload = self._post({"download_path": r"E:\New"}, settings)
        self.assertEqual(payload["changed"], 0)
        self.assertEqual(payload["updated_sources"], [])
        self.assertTrue(payload["folder_changed"])
        self.assertIn("folder download diperbarui", payload["message"])
        self.assertNotIn("0 manifest diperbarui", payload["message"])

    def test_unchanged_settings_report_no_change(self):
        settings = {**_settings(), "roxxy_download_path": r"E:\Movies"}
        payload = self._post({
            "manifests": {"jav": _manifest("jav")},
            "download_path": r"E:\Movies",
        }, settings)
        self.assertEqual(payload["changed"], 0)
        self.assertFalse(payload["folder_changed"])
        self.assertIn("tidak berubah", payload["message"])

    def test_replaced_manifest_is_named_in_the_message(self):
        replacement = "https://hub.roxxy-tech.com/" + "z" * 40 + "/manifest.json"
        payload = self._post({
            "manifests": {"jav": replacement},
            "download_path": r"E:\Movies",
        }, {**_settings(), "roxxy_download_path": r"E:\Movies"})
        self.assertEqual(payload["changed"], 1)
        self.assertEqual(payload["updated_sources"], ["jav"])
        self.assertIn("JAV", payload["message"])
        self.assertNotIn("z" * 40, payload["message"])

    def test_legacy_slot_resaved_is_not_reported_as_an_update(self):
        settings = {"roxxy_manifest_url": _manifest("english_adult")}
        payload = self._post({
            "manifest_url": _manifest("english_adult"),
            "download_path": r"E:\Movies",
        }, settings)
        self.assertEqual(payload["changed"], 0)
        self.assertEqual(payload["updated_sources"], [])


class TelemetryCategoryCountTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._old = (db.DB_DIR, db.DB_PATH, db.CACHE_DIR, db.BACKUP_DIR, db.PLAYLIST_DIR)
        for name, value in (
            ("DB_DIR", self.temp_dir.name),
            ("DB_PATH", os.path.join(self.temp_dir.name, "torbox_media_hub.db")),
            ("CACHE_DIR", os.path.join(self.temp_dir.name, "cache")),
            ("BACKUP_DIR", os.path.join(self.temp_dir.name, "backups")),
            ("PLAYLIST_DIR", os.path.join(self.temp_dir.name, "playlists")),
        ):
            setattr(db, name, value)
        db.init_db()

    def tearDown(self):
        (db.DB_DIR, db.DB_PATH, db.CACHE_DIR, db.BACKUP_DIR, db.PLAYLIST_DIR) = self._old
        self.temp_dir.cleanup()

    def _insert(self, unique_key, category, is_adult=0):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, content_category, is_adult, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unique_key, unique_key + ".mkv", unique_key, category, is_adult, "2026-01-01", "2026-01-01"),
            )

    def test_indonesia_movies_have_their_own_telemetry_bucket(self):
        self._insert("films-1", "indo_movie")
        self._insert("jav-1", "jav", 1)
        stats = db.get_telemetry_stats()
        self.assertEqual(stats["indo_movie_count"], 1)
        self.assertEqual(stats["jav_count"], 1)
        self.assertEqual(stats["total"], 2)


class HomeStatsTemplateTests(unittest.TestCase):
    def test_indonesia_movies_card_is_rendered_and_populated(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "index.html")
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        self.assertIn('id="stat-indo"', html)
        self.assertIn("stats.indo_movie_count", html)


class RoxxyTemplateGenreTests(unittest.TestCase):
    """The /roxxy template drives genre reloads and all-catalog search."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "roxxy.html")
        with open(path, encoding="utf-8") as fh:
            cls.template = fh.read()

    def test_genre_change_reloads_catalog_from_addon(self):
        self.assertIn("$('genre-select').addEventListener('change', applyGenreSelection)", self.template)
        self.assertIn("async function fetchCatalogPage(reset = false)", self.template)
        self.assertIn("fetchCatalogPage(true)", self.template)

    def test_required_genre_defaults_to_first_option(self):
        self.assertIn("required ? (options[0] || '')", self.template)

    def test_single_object_extra_is_normalized(self):
        self.assertIn("Array.isArray(c?.extra) ? c.extra : (c?.extra ? [c.extra] : [])", self.template)

    def test_all_search_only_contacts_searchable_catalogs(self):
        self.assertIn("if (query && catalogSupportsSearch(catalog)) p.set('search', query)", self.template)
        self.assertIn("activeCatalogs().filter(catalogSupportsSearch)", self.template)
        self.assertIn("searchableCatalogs()", self.template)

    def test_all_search_sends_required_genre(self):
        self.assertIn("fetchOneCatalog(c, st.skip, state.query, catalogRequiredGenre(c))", self.template)


class DownloadOptionGuardTests(unittest.TestCase):
    def test_unresolved_http_stream_disables_local_engines(self):
        options = rm._stream_download_options(
            {"engine": "pending_http_inspection", "resolved": False},
            "https://forwarder.example/1080p",
        )
        self.assertEqual([o["engine"] for o in options], ["yt-dlp", "aria2c"])
        for option in options:
            self.assertTrue(option["disabled"])
            self.assertIn("probe header", option["disabled_reason"])
            self.assertFalse(option.get("recommended"))

    def test_resolved_http_stream_keeps_buttons_enabled(self):
        options = rm._stream_download_options(
            {"engine": "yt-dlp", "resolved": True},
            "https://cdn.example/movie.m3u8",
        )
        self.assertFalse(any(o.get("disabled") for o in options))
        self.assertEqual([o["engine"] for o in options if o.get("recommended")], ["yt-dlp"])

    def test_torbox_routes_are_never_disabled(self):
        magnet = "magnet:?xt=urn:btih:" + "a" * 40
        options = rm._stream_download_options(
            {"engine": "torbox", "resolved": True, "magnet": magnet}, magnet
        )
        self.assertEqual([o["mode"] for o in options], ["cached", "uncached"])
        self.assertFalse(any(o.get("disabled") for o in options))


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class TorboxCacheBatchTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.hashes = ["a" * 40, "b" * 40]
        self.magnets = [f"magnet:?xt=urn:btih:{h}" for h in self.hashes]

    def _post(self, payload, data=None):
        body = {"success": True, "data": data if data is not None else []}
        with patch.object(rm.db, "get_settings", return_value={"torbox_api_key": "k"}), \
                patch.object(rm.requests, "get", return_value=_FakeResponse(body)) as get:
            response = self.client.post("/api/roxxy/torbox-cache-check", json=payload)
        return response, get

    def test_one_request_checks_every_magnet_and_maps_results(self):
        response, get = self._post({"magnets": self.magnets}, [{"hash": self.hashes[0].upper()}])
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(get.call_args.kwargs["params"]["hash"], ",".join(self.hashes))
        self.assertEqual(payload["results"], {self.hashes[0]: True, self.hashes[1]: False})
        self.assertEqual(payload["hashes"], self.hashes)
        self.assertTrue(payload["cached"])

    def test_single_magnet_keeps_legacy_fields(self):
        response, get = self._post({"magnet": self.magnets[0]}, [{"hash": self.hashes[0]}])
        payload = response.get_json()
        self.assertEqual(get.call_args.kwargs["params"]["hash"], self.hashes[0])
        self.assertTrue(payload["cached"])
        self.assertEqual(payload["hash"], self.hashes[0])
        self.assertEqual(payload["results"], {self.hashes[0]: True})

    def test_invalid_magnet_is_rejected_before_any_request(self):
        response, get = self._post({"magnets": self.magnets + ["https://hoster.example/file"]})
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_batch_is_capped_at_one_hundred_hashes(self):
        magnets = [f"magnet:?xt=urn:btih:{i:040x}" for i in range(101)]
        response, get = self._post({"magnets": magnets})
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()


class ForwardedPayloadTests(unittest.TestCase):
    @staticmethod
    def _forward(payload, host="rt-openclaw.roxxy-tech.com", path="/nzb/dl"):
        token = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        return f"https://{host}{path}?d={token}"

    def test_nzb_payload_keys_are_all_accepted(self):
        for key in ("n", "nzb", "u", "url", "link", "download", "file"):
            url = self._forward({key: "https://hoster.example/release.nzb"})
            self.assertEqual(rm._decode_forwarded_nzb(url), "https://hoster.example/release.nzb")

    def test_nzb_payload_accepts_percent_encoded_link(self):
        url = self._forward({"u": "https%3A%2F%2Fhoster.example%2Ffile.nzb"})
        self.assertEqual(rm._decode_forwarded_nzb(url), "https://hoster.example/file.nzb")

    def test_nzb_explicit_field_works_on_a_new_forwarder_path(self):
        # Generic decoder: the explicit `nzb` field is trusted on any path.
        for path in ("/dl", "/v2/nzb/download", "/release.nzb"):
            url = self._forward({"nzb": "https://hoster.example/file.nzb"}, path=path)
            self.assertEqual(rm._decode_forwarded_nzb(url), "https://hoster.example/file.nzb")

    def test_nzb_weak_alias_needs_an_nzb_path(self):
        # A forwarded media payload on a non-NZB path must not become Usenet.
        self.assertIsNone(rm._decode_forwarded_nzb(
            self._forward({"u": "https://cdn.example/movie.m3u8"}, path="/hls/dl")))

    def test_bare_url_payload_is_decoded_for_nzb_paths(self):
        token = base64.urlsafe_b64encode(b"https://hoster.example/release.nzb").decode().rstrip("=")
        self.assertEqual(
            rm._decode_forwarded_nzb(f"https://rt-openclaw.roxxy-tech.com/nzb/dl?d={token}"),
            "https://hoster.example/release.nzb",
        )

    def test_forwarder_host_allowlist_covers_the_domain_only(self):
        payload = {"u": "https://hoster.example/release.nzb"}
        allowed = [
            "rt-openclaw.roxxy-tech.com",
            "hub.roxxy-tech.com",
            "roxxy-tech.com",
        ]
        rejected = [
            "evil.example",
            "roxxy-tech.com.evil.example",
            "notroxxy-tech.com",
        ]
        for host in allowed:
            self.assertEqual(
                rm._decode_forwarded_nzb(self._forward(payload, host=host)),
                "https://hoster.example/release.nzb",
            )
        for host in rejected:
            self.assertIsNone(rm._decode_forwarded_nzb(self._forward(payload, host=host)))

    def test_forwarder_allowlist_can_be_extended_by_env(self):
        with patch.object(rm, "ROXXY_FORWARD_DOMAINS", ("example.net",)):
            payload = {"u": "https://hoster.example/release.nzb"}
            self.assertEqual(
                rm._decode_forwarded_nzb(self._forward(payload, host="cdn.example.net")),
                "https://hoster.example/release.nzb",
            )
            self.assertIsNone(rm._decode_forwarded_nzb(self._forward(payload)))

    def test_oversized_or_broken_payloads_are_ignored(self):
        long_token = base64.urlsafe_b64encode(b"x" * 9000).decode()
        self.assertIsNone(rm._decode_forwarded_nzb(
            f"https://rt-openclaw.roxxy-tech.com/nzb/dl?d={long_token}"))
        self.assertIsNone(rm._decode_forwarded_nzb(
            "https://rt-openclaw.roxxy-tech.com/nzb/dl?d=###"))

    def test_nzb_forwarder_rejects_unknown_host_and_plain_links(self):
        payload = {"u": "https://hoster.example/release.nzb"}
        self.assertIsNone(rm._decode_forwarded_nzb(self._forward(payload, host="evil.example")))
        self.assertIsNone(rm._decode_forwarded_nzb(self._forward(payload, path="/other/dl")))
        self.assertIsNone(rm._decode_forwarded_nzb("https://hoster.example/release.nzb"))

    def test_magnet_forwarder_extracts_only_real_magnets(self):
        magnet = "magnet:?xt=urn:btih:" + "c" * 40
        url = self._forward({"m": magnet}, path="/torrent/dl")
        self.assertEqual(rm._decode_forwarded_magnet(url), magnet)
        self.assertEqual(
            rm._decode_forwarded_magnet(self._forward({"magnet": magnet}, path="/torrent/dl")),
            magnet,
        )
        self.assertIsNone(rm._decode_forwarded_magnet(
            self._forward({"m": "not-a-magnet"}, path="/torrent/dl")))
        self.assertIsNone(rm._decode_forwarded_magnet(
            self._forward({"m": magnet}, path="/nzb/dl")))

    def test_forwarded_media_payload_is_not_routed_to_usenet(self):
        token = base64.urlsafe_b64encode(
            json.dumps({"u": "https://cdn.example/movie.m3u8"}).encode()
        ).decode().rstrip("=")
        analysis = rm._classify_stream(
            {"url": f"https://rt-openclaw.roxxy-tech.com/hls/dl?d={token}", "name": "Movie"}
        )
        self.assertIsNone(analysis["nzb_url"])
        self.assertNotEqual(analysis["engine"], "torbox")

    def test_nzb_url_detection_covers_paths_and_extensions(self):
        self.assertTrue(rm._looks_like_nzb_url("https://hoster.example/release.nzb"))
        self.assertTrue(rm._looks_like_nzb_url("https://hoster.example/nzb/dl?x=1"))
        self.assertFalse(rm._looks_like_nzb_url("https://hoster.example/release.mkv"))


class UsenetLinkValidationTests(unittest.TestCase):
    def test_valid_links_pass(self):
        for url in (
            "https://hoster.example/release.nzb",
            "https://rt-openclaw.roxxy-tech.com/nzb/dl?d=x",
            "http://93.184.216.34/file.nzb",
        ):
            safe, error = app_module._validate_usenet_link(url)
            self.assertTrue(safe, msg=f"{url} -> {error}")

    def test_unusable_links_are_rejected(self):
        cases = {
            "ftp://hoster.example/release.nzb": "http atau https",
            "https://user:pass@hoster.example/release.nzb": "kredensial",
            "https://localhost/release.nzb": "jaringan lokal",
            "http://127.0.0.1/release.nzb": "IP privat",
            "http://10.0.0.5/release.nzb": "IP privat",
            "https://files.internal/release.nzb": "jaringan lokal",
            "": "wajib diisi",
        }
        for url, expected in cases.items():
            safe, error = app_module._validate_usenet_link(url)
            self.assertFalse(safe, msg=url)
            self.assertIn(expected, error, msg=url)

    def test_overlong_links_are_rejected(self):
        safe, error = app_module._validate_usenet_link(
            "https://hoster.example/" + "a" * 2100
        )
        self.assertFalse(safe)
        self.assertIn("terlalu panjang", error)


class AddCachedUsenetTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def _post(self, payload):
        with patch.object(app_module.db, "get_settings", return_value={"torbox_api_key": "k"}), \
                patch.object(app_module.meta_eng.HTTP_SESSION, "post") as post, \
                patch.object(app_module.TORBOX_MONITOR, "track") as track:
            response = self.client.post("/api/torbox/add-cached", json=payload)
        return response, post, track

    def test_unsafe_nzb_link_is_rejected_before_torbox(self):
        response, post, track = self._post(
            {"kind": "usenet", "nzb_url": "ftp://hoster.example/release.nzb"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])
        post.assert_not_called()
        track.assert_not_called()

    def test_valid_nzb_link_is_sent_as_usenet_link(self):
        response = None
        with patch.object(app_module.db, "get_settings", return_value={"torbox_api_key": "k"}), \
                patch.object(app_module.meta_eng.HTTP_SESSION, "post") as post, \
                patch.object(app_module.TORBOX_MONITOR, "track") as track:
            post.return_value.status_code = 200
            post.return_value.content = b'{"success": true, "data": {"usenet_id": 7}}'
            post.return_value.json.return_value = {"success": True, "data": {"usenet_id": 7}}
            response = self.client.post(
                "/api/torbox/add-cached",
                json={"kind": "usenet", "nzb_url": "https://hoster.example/release.nzb", "title": "Release"},
            )
        self.assertEqual(response.status_code, 200)
        files = post.call_args.kwargs["files"]
        self.assertEqual(files["link"], (None, "https://hoster.example/release.nzb"))
        track.assert_called_once()


class ManifestCategoryLockTests(unittest.TestCase):
    def test_addon_locked_categories_ignore_hint_classification(self):
        with patch.object(app_module, "_resolve_import_category") as resolve:
            for category in ("english_adult", "indo_movie", "donghua"):
                self.assertEqual(
                    app_module._apply_manifest_category_lock(
                        category, {"name": "Film Indonesia"}, "file.mkv"
                    ),
                    category,
                )
            resolve.assert_not_called()

    def test_unspecific_category_uses_hint_verdict(self):
        with patch.object(app_module, "_resolve_import_category", return_value="jav") as resolve:
            self.assertEqual(
                app_module._apply_manifest_category_lock(
                    "general_movie", {"name": "ABP-123"}, "ABP-123.mkv", "Folder"
                ),
                "jav",
            )
            resolve.assert_called_once()

    def test_empty_category_uses_the_manifest_selector(self):
        with patch.object(app_module, "_resolve_import_category") as resolve:
            self.assertEqual(
                app_module._apply_manifest_category_lock(
                    "", {"name": "Soul Land", "_roxxySource": "donghua"}, "05.mkv"
                ),
                "donghua",
            )
            self.assertEqual(
                app_module._apply_manifest_category_lock(
                    "", {"name": "Massage", "_roxxySource": "onlyfans"}, "a.mp4"
                ),
                "english_adult",
            )
            resolve.assert_not_called()

    def test_manifest_selector_wins_over_a_stale_client_category(self):
        self.assertEqual(
            app_module._apply_manifest_category_lock(
                "english_adult",
                {"name": "Film Indonesia", "_roxxySource": "indo_movie"},
                "film.mkv",
            ),
            "indo_movie",
        )

    def test_jav_is_not_provenance_locked(self):
        # Decision: the JAV Library needs a verified production code, so the
        # addon selector alone cannot file a code-less title as JAV.
        self.assertNotIn("jav", app_module._MANIFEST_LOCKED_CATEGORIES)
        self.assertEqual(rm.source_category("jav"), "")
        with patch.object(app_module, "_resolve_import_category", return_value="jav") as resolve:
            self.assertEqual(
                app_module._apply_manifest_category_lock(
                    "", {"name": "SSIS-123", "_roxxySource": "jav"}, "SSIS-123.mkv"
                ),
                "jav",
            )
            resolve.assert_called_once()

    def test_adult_only_addon_never_lands_in_a_family_category(self):
        with patch.object(app_module, "_resolve_import_category", return_value="tv_show"):
            self.assertEqual(
                app_module._apply_manifest_category_lock(
                    "", {"name": "Uncensored Leak", "_roxxySource": "jav"}, "E01.mkv"
                ),
                "uncategorized",
            )

    def test_unknown_category_falls_back_to_uncategorized(self):
        self.assertEqual(
            app_module._apply_manifest_category_lock("bogus", {}, ""),
            "uncategorized",
        )


class ReimportCategoryLockTests(unittest.TestCase):
    """A second classification pass must not undo the manifest category."""

    @staticmethod
    def _verdict(category):
        return {
            "classification_status": "classified",
            "detected_category": category,
            "classification_method": "provider_media_type_series",
            "category_confidence": 94,
            "signals": [],
            "warnings": [],
        }

    def test_donghua_import_stays_donghua(self):
        guarded = app_module._guard_adult_reclassification(
            {"content_category": "donghua"}, self._verdict("tv_show")
        )
        self.assertEqual(guarded["classification_status"], "needs_review")
        self.assertIsNone(guarded["detected_category"])
        self.assertTrue(guarded["warnings"])

    def test_indo_movie_import_stays_indo_movie(self):
        guarded = app_module._guard_adult_reclassification(
            {"content_category": "indo_movie"}, self._verdict("general_movie")
        )
        self.assertEqual(guarded["classification_status"], "needs_review")
        self.assertIsNone(guarded["detected_category"])

    def test_english_adult_can_still_be_promoted_to_indo(self):
        guarded = app_module._guard_adult_reclassification(
            {"content_category": "english_adult"}, self._verdict("indo")
        )
        self.assertEqual(guarded["detected_category"], "indo")
        self.assertEqual(guarded["classification_status"], "classified")

    def test_uncategorized_jav_import_is_not_refiled_as_family(self):
        guarded = app_module._guard_adult_reclassification(
            {"content_category": "uncategorized"},
            self._verdict("tv_show"),
            source="jav",
        )
        self.assertEqual(guarded["classification_status"], "needs_review")
        self.assertIsNone(guarded["detected_category"])

    def test_adult_source_check_needs_the_selector(self):
        guarded = app_module._guard_adult_reclassification(
            {"content_category": "uncategorized"}, self._verdict("tv_show")
        )
        self.assertEqual(guarded["detected_category"], "tv_show")


class ImportHintSourceTests(unittest.TestCase):
    def test_known_addon_selector_survives_sanitising(self):
        cleaned = app_module._sanitize_import_metadata(
            {"name": "Film", "_roxxySource": " Indo_Movie ", "signed_url": "x"}
        )
        self.assertEqual(cleaned["_roxxySource"], "indo_movie")
        self.assertNotIn("signed_url", cleaned)

    def test_unknown_selector_is_dropped(self):
        cleaned = app_module._sanitize_import_metadata({"name": "Film", "_roxxySource": "../../evil"})
        self.assertNotIn("_roxxySource", cleaned)


class LocalIdentifyCategoryTests(unittest.TestCase):
    """D2 classification uses the same one-definition source policy."""

    @staticmethod
    def _classification(category, code=None):
        return {
            "classification_status": "classified",
            "detected_category": category,
            "classification_method": "provider_media_type_series",
            "category_confidence": 94,
            "signals": [],
            "warnings": [],
            "detected_code": code,
        }

    def test_donghua_source_forces_the_addon_category(self):
        with patch.object(
            engine.classification_engine,
            "classify_media",
            return_value=self._classification("tv_show"),
        ):
            result = engine._identify_roxxy_media(
                {
                    "file_path": "Soul.Land.S01E03.mkv",
                    "metadata": {"name": "Soul Land", "_roxxySource": "donghua"},
                },
                {"size": 10},
            )
        identification = result["validation"]["d2_identification"]
        self.assertEqual(identification["category"], "donghua")
        self.assertEqual(identification["classification_method"], "roxxy_source_donghua")
        self.assertEqual(result["status"], "identified")

    def test_jav_source_stays_code_verified(self):
        with patch.object(
            engine.classification_engine,
            "classify_media",
            return_value=self._classification("jav", code=None),
        ):
            result = engine._identify_roxxy_media(
                {
                    "file_path": "Uncensored.Leak.mp4",
                    "metadata": {"name": "Uncensored Leak", "_roxxySource": "jav"},
                },
                {"size": 10},
            )
        identification = result["validation"]["d2_identification"]
        self.assertEqual(identification["category"], "jav")
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(
            any("verified production code" in warning for warning in identification["warnings"])
        )


class RoxxyTemplateAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "roxxy.html")
        with open(path, encoding="utf-8") as fh:
            cls.template = fh.read()

    def test_unresolved_options_render_as_disabled_buttons(self):
        self.assertIn("if (option.disabled)", self.template)
        self.assertIn("perlu probe header", self.template)

    def test_cache_checks_are_batched_into_one_request(self):
        self.assertIn("checkTorboxCacheBatch", self.template)
        self.assertIn("JSON.stringify({ magnets })", self.template)
        self.assertIn("queueTorboxCacheCheck(i)", self.template)
        self.assertNotIn("checkTorboxCache(i);", self.template)

    def test_ui_source_category_map_matches_the_server_policy(self):
        # The server re-derives the category from `_roxxySource`; if the UI map
        # drifts it must drift in the same direction. JAV stays absent.
        match = re.search(r"const map = \{([^}]*)\};", self.template)
        self.assertIsNotNone(match)
        pairs = dict(re.findall(r"(\w+): '([^']*)'", match.group(1)))
        self.assertEqual(pairs, rm.ROXXY_SOURCE_CATEGORIES)
        self.assertNotIn("jav", pairs)

    def test_catalog_filter_has_its_own_dropdown(self):
        self.assertIn('id="catalog-filter-select"', self.template)
        self.assertIn("$('catalog-filter-select').addEventListener('change', applyCatalogSearchFilter)", self.template)
        self.assertIn("const raw = $('catalog-filter-select')?.value || ''", self.template)
        # The genre control stays dedicated to the addon's genre extra.
        self.assertNotIn("$('genre-select').innerHTML = `<option value=\"cat:", self.template)

    def test_toolbar_swaps_genre_for_catalog_filter_in_all_mode(self):
        self.assertIn("function updateCatalogControls(allMode)", self.template)
        self.assertIn("genre.classList.toggle('hidden', !!allMode)", self.template)
        self.assertIn("filter.classList.toggle('hidden', !allMode)", self.template)
        self.assertIn("updateCatalogControls(allMode)", self.template)


if __name__ == "__main__":
    unittest.main()
