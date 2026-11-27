import json
import time
import unittest
from unittest.mock import patch

import app as app_module
import webdl_resolver as webdl


class FakeResponse:
    def __init__(self, status_code=200, body=None, content=b"{}"):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.content = content
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class WebdlResolverTests(unittest.TestCase):
    def test_supported_provider_pages(self):
        cases = {
            "Pornhub": ("6afdf239a5d7c", "https://www.pornhub.com/view_video.php?viewkey=6afdf239a5d7c"),
            "Eporner": ("EeSURFfHeBw", "https://www.eporner.com/video-EeSURFfHeBw"),
            "XHamster": ("xhau06C", "https://xhamster.com/videos/xhau06C"),
            "RedGifs": ("punctualwholefossa", "https://www.redgifs.com/watch/punctualwholefossa"),
            "XVideos": ("kmuhovfe970", "https://www.xvideos.com/embedframe/kmuhovfe970"),
            "YouJizz": ("129526361", "https://www.youjizz.com/videos/embed/129526361"),
            "TNAFlix": ("26330848", "https://player.tnaflix.com/video/26330848"),
            "EMPFlix": ("26330849", "https://player.empflix.com/video/26330849"),
        }
        for provider, (provider_id, expected) in cases.items():
            with self.subTest(provider=provider):
                raw_id = f"{provider.lower()}:{provider_id}"
                self.assertEqual(webdl.resolve_page_url(provider, raw_id), expected)

    def test_unknown_provider_is_not_resolvable(self):
        self.assertEqual(webdl.resolve_page_url("WhoresHub", "whoreshub:abc"), "")
        self.assertEqual(webdl.resolve_page_url("", "abc"), "")

    def test_unsafe_provider_ids_are_rejected(self):
        for bad_id in ("../../etc/passwd", "abc?x=1", "abc#frag", "a" * 65):
            with self.subTest(bad_id=bad_id):
                self.assertEqual(webdl.resolve_page_url("Pornhub", bad_id), "")

    def test_validate_webdl_url(self):
        ok, _ = webdl.validate_webdl_url("https://www.terabox.com/s/abc123")
        self.assertTrue(ok)

        rejected = (
            "http://127.0.0.1/video",
            "http://localhost/video",
            "ftp://example.com/video",
            "https://rt-openclaw.roxxy-tech.com/hls/segment",
            "https://open.spotify.com/track/1",
            "https://www.netflix.com/watch/1",
            "https://user:pass@example.com/video",
        )
        for url in rejected:
            with self.subTest(url=url):
                ok, error = webdl.validate_webdl_url(url)
                self.assertFalse(ok)
                self.assertTrue(error)


class WebdlEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        app_module._WEB_DL_HOSTERS_CACHE.update({"names": [], "fetched_at": 0.0, "error": ""})
        app_module._WEB_DL_SUBMIT_TIMES.clear()
        app_module._WEB_DL_RECENT_LINKS.clear()

    def tearDown(self):
        app_module._WEB_DL_SUBMIT_TIMES.clear()
        app_module._WEB_DL_RECENT_LINKS.clear()

    def _settings(self):
        return patch.object(
            app_module.db, "get_settings", return_value={"torbox_api_key": "test-key"}
        )

    def test_info_caches_hosters_for_a_day(self):
        fake = FakeResponse(200, {"success": True, "data": [{"name": "Terabox"}, {"name": "Mega"}]})
        with self._settings(), patch.object(
            app_module.meta_eng.HTTP_SESSION, "get", return_value=fake
        ) as mocked_get:
            first = self.client.get("/api/torbox/webdl/info")
            second = self.client.get("/api/torbox/webdl/info")

        self.assertEqual(first.status_code, 200)
        body = first.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["hoster_count"], 2)
        self.assertEqual(body["hosters"], ["Mega", "Terabox"])
        self.assertEqual(mocked_get.call_count, 1)
        provider_ids = {p["id"] for p in second.get_json()["providers"]}
        self.assertIn("redgifs", provider_ids)
        self.assertIn("pornhub", provider_ids)

    def test_add_provider_resolves_page_url_and_tracks(self):
        fake = FakeResponse(
            200,
            {
                "success": True,
                "data": {"webdownload_id": 2465447},
                "detail": "Video download started.",
            },
        )
        posted = {}

        def fake_post(url, headers=None, files=None, timeout=None):
            posted["url"] = url
            posted["headers"] = headers
            posted["files"] = files
            return fake

        with self._settings(), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post", side_effect=fake_post
        ), patch.object(app_module.TORBOX_MONITOR, "track") as track:
            response = self.client.post(
                "/api/torbox/webdl/add",
                json={
                    "provider": "RedGifs",
                    "provider_id": "redgifs:punctualwholefossa",
                    "title": "Probe",
                    "metadata": {"name": "Probe"},
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["tracked_id"], "2465447")
        self.assertIn("Auto-monitoring", body["message"])
        self.assertTrue(posted["url"].endswith("/webdl/createwebdownload"))
        self.assertEqual(
            posted["files"]["link"][1], "https://www.redgifs.com/watch/punctualwholefossa"
        )
        self.assertEqual(posted["files"]["as_queued"], (None, "false"))
        self.assertEqual(posted["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(track.call_args.kwargs["kind"], "webdl")
        self.assertEqual(track.call_args.kwargs["item_id"], "2465447")
        self.assertEqual(len(app_module._WEB_DL_SUBMIT_TIMES), 1)

    def test_add_pasted_hoster_url(self):
        fake = FakeResponse(200, {"success": True, "data": {"webdownload_id": 99}})
        with self._settings(), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post", return_value=fake
        ) as mocked_post, patch.object(app_module.TORBOX_MONITOR, "track"):
            response = self.client.post(
                "/api/torbox/webdl/add",
                json={"url": "https://www.terabox.com/s/abc123", "title": "TeraBox item"},
            )

        self.assertEqual(response.status_code, 200)
        files = mocked_post.call_args.kwargs["files"]
        self.assertEqual(files["link"][1], "https://www.terabox.com/s/abc123")
        self.assertEqual(files["name"][1], "TeraBox item")

    def test_unsupported_host_is_rejected_before_api_call(self):
        with self._settings(), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post"
        ) as mocked_post:
            response = self.client.post(
                "/api/torbox/webdl/add", json={"url": "https://open.spotify.com/track/1"}
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("tidak didukung", response.get_json()["error"])
        mocked_post.assert_not_called()

    def test_unsupported_provider_without_link_asks_for_manual_url(self):
        response = self.client.post(
            "/api/torbox/webdl/add",
            json={"provider": "WhoresHub", "provider_id": "whoreshub:abc"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tempel link", response.get_json()["error"])

    def test_duplicate_link_is_rejected(self):
        url = "https://www.terabox.com/s/abc123"
        app_module._WEB_DL_RECENT_LINKS[url] = time.time()
        with self._settings(), patch.object(app_module.meta_eng.HTTP_SESSION, "post") as mocked_post:
            response = self.client.post("/api/torbox/webdl/add", json={"url": url})
        self.assertEqual(response.status_code, 429)
        self.assertIn("baru saja dikirim", response.get_json()["error"])
        mocked_post.assert_not_called()

    def test_hourly_limit_is_enforced(self):
        now = time.time()
        app_module._WEB_DL_SUBMIT_TIMES.extend([now] * 60)
        with self._settings(), patch.object(app_module.meta_eng.HTTP_SESSION, "post") as mocked_post:
            response = self.client.post(
                "/api/torbox/webdl/add", json={"url": "https://www.terabox.com/s/abc123"}
            )
        self.assertEqual(response.status_code, 429)
        self.assertIn("60", response.get_json()["error"])
        mocked_post.assert_not_called()

    def test_remote_unsupported_site_error_is_translated(self):
        fake = FakeResponse(
            400,
            {
                "success": False,
                "detail": "The site you are trying to download from is not supported.",
            },
        )
        with self._settings(), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post", return_value=fake
        ), patch.object(app_module.TORBOX_MONITOR, "track") as track:
            response = self.client.post(
                "/api/torbox/webdl/add",
                json={"url": "https://www.terabox.com/s/abc123"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Local Engine", response.get_json()["error"])
        track.assert_not_called()
        self.assertEqual(len(app_module._WEB_DL_SUBMIT_TIMES), 0)

    def test_missing_api_key_is_reported(self):
        with patch.object(app_module.db, "get_settings", return_value={}):
            response = self.client.post(
                "/api/torbox/webdl/add", json={"url": "https://www.terabox.com/s/abc123"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("API Key", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()