"""Offline tests for the optional direct-stream extraction (adult card modal).

Every fixture is synthetic markup; no test touches the network. The adapters
only read URLs a provider page publishes, so the fixtures below reproduce the
exact shapes observed on the live pages without carrying any provider content.
"""

import unittest
from unittest.mock import patch

import adult_discovery as adult_disc
import adult_streams as streams
import app as app_module


def item(provider, provider_id="Sample0001", title="Sample Adult Item"):
    return {
        "provider": provider,
        "provider_id": provider_id,
        "title": title,
        "page_url": {
            "eporner": f"https://www.eporner.com/video-{provider_id}/sample/",
            "xvideos": f"https://www.xvideos.com/video.{provider_id}/sample",
            "xnxx": f"https://www.xnxx.com/video-{provider_id}/sample",
            "youjizz": f"https://www.youjizz.com/videos/sample-{provider_id}.html",
            "xxxymovies": f"https://xxxymovies.com/videos/{provider_id}/sample/",
            "sunporno": f"https://www.sunporno.com/v/{provider_id}/sample/",
            "pornhub": "https://www.pornhub.com/view_video.php?viewkey=sample",
            "xhamster": "https://xhamster.com/videos/sample-xhzSample",
        }[provider],
    }


class EpornerParserTests(unittest.TestCase):
    def test_embed_page_url_is_extracted_once(self):
        markup = (
            '<video data-vid="Sample0001/Sample0001.mp4"></video>'
            '<script>EP.video.player.url = '
            '"https://gvideo.eporner.com/Sample0001/Sample0001.mp4";</script>'
        )
        rows = streams._parse_eporner(markup, "Sample")
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["url"],
            "https://gvideo.eporner.com/Sample0001/Sample0001.mp4",
        )


class Html5PlayerParserTests(unittest.TestCase):
    def test_high_and_hls_are_kept_low_is_deduplicated(self):
        markup = """
        html5player.setVideoUrlHigh('https://mp4-cdn.example.com/sample/mp4_sd.mp4?token=1');
        html5player.setVideoUrlLow('https://mp4-cdn.example.com/sample/mp4_sd.mp4?token=1');
        html5player.setVideoHLS('https://hls-cdn.example.com/sample/hls.m3u8?token=2');
        """
        rows = streams._parse_html5player(markup, "Sample")
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["quality"] for row in rows], ["SD", "HLS"])


class YouJizzParserTests(unittest.TestCase):
    def test_data_encodings_are_split_by_quality(self):
        markup = (
            '<script> var dataEncodings = ['
            '{"quality":"720","filename":"\\/\\/cdn.example.com\\/a-720.mp4?x=1",'
            '"name":"720p"},'
            '{"quality":"720","filename":"\\/\\/hls.example.com\\/a-720.mp4\\/master.m3u8",'
            '"name":"720p"}]; </script>'
        )
        rows = streams._parse_youjizz(markup, "Sample")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["quality"] for row in rows}, {"720p"})
        self.assertEqual({row["label"] for row in rows}, {"MP4", "HLS"})


class FlashvarsParserTests(unittest.TestCase):
    def test_login_alternative_is_ignored(self):
        markup = """
        var flashvars = {
          video_url: 'https://cdn.example.com/get_file/1/abc/1/1.mp4/',
          video_url_hd: '1',
          video_url_text: 'High Definition',
          video_alt_url: 'https://provider.example.com/signup.php',
          video_alt_url_text: 'Login',
        };
        """
        rows = streams._parse_flashvars(markup, "Sample")
        self.assertEqual(len(rows), 1)
        self.assertIn("/get_file/", rows[0]["url"])


class PornhubParserTests(unittest.TestCase):
    def test_media_definitions_are_read(self):
        markup = """
        var flashvars_123 = {"mediaDefinitions": [
          {"format":"mp4","quality":"720","videoUrl":"https://cdn.example.com/720.mp4"},
          {"format":"hls","quality":"1080","videoUrl":"https://cdn.example.com/1080.m3u8"}
        ], "other":"x"};
        """
        rows = streams._parse_pornhub(markup, "Sample")
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["quality"] for row in rows}, {"720p", "1080p"})


class MediaUrlTests(unittest.TestCase):
    def test_protocol_relative_and_http_are_normalised(self):
        self.assertEqual(
            streams._media_url("//cdn.example.com/a.mp4"),
            "https://cdn.example.com/a.mp4",
        )
        self.assertEqual(
            streams._media_url("http://cdn.example.com/a.mp4"),
            "https://cdn.example.com/a.mp4",
        )

    def test_unsafe_or_private_urls_are_rejected(self):
        for raw in (
            "",
            "javascript:alert(1)",
            "https://user:pass@cdn.example.com/a.mp4",
            "https://127.0.0.1/a.mp4",
            "https://localhost/a.mp4",
            "data:video/mp4;base64,AAAA",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(streams._media_url(raw), "")

    def test_filenames_are_sanitised_and_keep_the_extension(self):
        name = streams._filename('Bad / title: "sample"', "https://x/a.m3u8")
        self.assertTrue(name.endswith(".m3u8"))
        for char in '<>:"/\\|?*':
            self.assertNotIn(char, name)


class PageMetadataTests(unittest.TestCase):
    def test_open_graph_cover_and_synopsis_come_from_the_page(self):
        markup = (
            '<html><head>'
            '<meta property="og:title" content="Sample &amp; Title">'
            '<meta property="og:description" content="Short social blurb.">'
            '<meta property="og:image" content="https://cdn.example.com/cover.jpg">'
            '<script type="application/ld+json">'
            '{"@type":"VideoObject","name":"Sample &amp; Title",'
            '"description":"A much longer synopsis shipped in JSON-LD.",'
            '"duration":"PT12M34S",'
            '"thumbnailUrl":["https://cdn.example.com/alt.jpg"]}'
            "</script></head></html>"
        )
        meta = streams.page_metadata(markup)
        self.assertEqual(meta["title"], "Sample & Title")
        self.assertEqual(meta["description"], "A much longer synopsis shipped in JSON-LD.")
        self.assertEqual(meta["poster"], "https://cdn.example.com/cover.jpg")
        self.assertEqual(meta["duration_sec"], 12 * 60 + 34)

    def test_private_or_malformed_cover_is_dropped(self):
        markup = (
            '<meta property="og:image" content="http://127.0.0.1/cover.jpg">'
            '<meta property="og:description" content="Synopsis stays.">'
        )
        meta = streams.page_metadata(markup)
        self.assertNotIn("poster", meta)
        self.assertEqual(meta["description"], "Synopsis stays.")

    def test_largest_jsonld_cover_wins_over_the_small_og_one(self):
        markup = (
            '<meta property="og:image" content="https://cdn.example.com/8_240.jpg">'
            '<script type="application/ld+json">'
            '{"@type":"VideoObject","thumbnailUrl":['
            '"https://cdn.example.com/8_360.jpg","https://cdn.example.com/8_720.jpg"]}'
            "</script>"
        )
        meta = streams.page_metadata(markup)
        self.assertEqual(meta["poster"], "https://cdn.example.com/8_720.jpg")

    def test_generic_age_wall_title_is_not_used(self):
        markup = (
            '<meta property="og:title" content="Age-restricted content">'
            '<meta property="og:description" content="Real synopsis survives.">'
        )
        meta = streams.page_metadata(markup)
        self.assertNotIn("title", meta)
        self.assertEqual(meta["description"], "Real synopsis survives.")

    def test_markup_without_metadata_returns_nothing(self):
        self.assertEqual(streams.page_metadata(""), {})
        self.assertEqual(streams.page_metadata("<html><body>plain</body></html>"), {})


class StreamListTests(unittest.TestCase):
    def test_deduplicate_sort_and_normalise(self):
        rows = [
            {"url": "https://cdn.example.com/360.mp4", "quality": "360"},
            {"url": "https://cdn.example.com/1080.mp4", "quality": "1080"},
            {"url": "https://cdn.example.com/1080.mp4", "quality": "1080"},
            {"url": "https://cdn.example.com/master.m3u8", "quality": "", "label": "HLS"},
        ]
        result = streams._finalise("youjizz", "Sample Title", rows)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["quality"], "1080p")
        self.assertEqual(result[0]["route_hint"], "aria2_candidate")
        self.assertTrue(result[0]["behaviorHints"]["filename"].endswith(".mp4"))
        hls = [s for s in result if s["route_hint"] == "yt_dlp_candidate"]
        self.assertEqual(len(hls), 1)


class StreamsForTests(unittest.TestCase):
    def setUp(self):
        streams.reset_state_for_tests()
        adult_disc.reset_state_for_tests()

    def test_unsupported_provider_returns_an_honest_warning_without_fetching(self):
        with patch.object(streams, "_fetch_html") as fetch:
            result = streams.streams_for(item("xhamster"))
        fetch.assert_not_called()
        self.assertEqual(result["streams"], [])
        self.assertEqual(result["warnings"][0]["code"], "streams_unsupported")

    def test_fetch_failure_raises_discovery_error(self):
        with patch.object(streams, "_fetch_html", return_value={"ok": False, "code": "blocked"}), patch.object(
            streams.time, "sleep", return_value=None
        ):
            with self.assertRaises(adult_disc.AdultDiscoveryError) as ctx:
                streams.streams_for(item("eporner"))
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("Cloudflare", str(ctx.exception))

    def test_success_is_cached_until_forced(self):
        markup = '<script>EP.video.player.url="https://gvideo.eporner.com/Sample0001/Sample0001.mp4";</script>'
        with patch.object(streams, "_fetch_html", return_value={"ok": True, "html": markup}) as fetch:
            first = streams.streams_for(item("eporner"))
            second = streams.streams_for(item("eporner"))
            self.assertEqual(fetch.call_count, 1)
            self.assertFalse(first["cached"])
            self.assertTrue(second["cached"])
            streams.streams_for(item("eporner"), force=True)
            self.assertEqual(fetch.call_count, 2)

    def test_provider_metadata_accompanies_streams_and_survives_the_cache(self):
        markup = (
            '<meta property="og:image" content="https://cdn.example.com/cover.jpg">'
            '<meta property="og:description" content="Provider synopsis.">'
            '<script>EP.video.player.url="https://gvideo.eporner.com/Sample0001/Sample0001.mp4";</script>'
        )
        with patch.object(streams, "_fetch_html", return_value={"ok": True, "html": markup}):
            first = streams.streams_for(item("eporner"))
            second = streams.streams_for(item("eporner"))
        self.assertEqual(first["page_meta"]["description"], "Provider synopsis.")
        self.assertEqual(second["page_meta"]["poster"], "https://cdn.example.com/cover.jpg")
        self.assertTrue(second["cached"])

    def test_xvideos_embed_shell_card_yields_to_the_real_page(self):
        embed = (
            '<meta property="og:title" content="Xvideos.com embed video">'
            "<script>setVideoUrlHigh('https://cdn.example.com/sd.mp4');</script>"
        )
        real = (
            '<meta property="og:title" content="Sample Real Title">'
            '<meta property="og:description" content="Real synopsis.">'
            '<meta property="og:image" content="https://cdn.example.com/real.jpg">'
        )
        real_url = "https://www.xvideos.com/video.Sample0001/sample"

        def fake(url, provider):
            return {"ok": True, "html": real if url == real_url else embed}

        with patch.object(streams, "_fetch_html", side_effect=fake) as fetch:
            result = streams.streams_for(item("xvideos"))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result["page_meta"]["title"], "Sample Real Title")
        self.assertEqual(result["page_meta"]["description"], "Real synopsis.")
        self.assertEqual(result["page_meta"]["poster"], "https://cdn.example.com/real.jpg")
        self.assertEqual(len(result["streams"]), 1)

    def test_real_page_failure_keeps_the_stream_and_skips_the_card(self):
        embed = "<script>setVideoUrlHigh('https://cdn.example.com/sd.mp4');</script>"

        def fake(url, provider):
            if url == "https://www.xvideos.com/video.Sample0001/sample":
                return {"ok": False, "code": "blocked"}
            return {"ok": True, "html": embed}

        with patch.object(streams, "_fetch_html", side_effect=fake):
            result = streams.streams_for(item("xvideos"))
        self.assertEqual(len(result["streams"]), 1)
        self.assertEqual(result["page_meta"], {})

    def test_page_url_must_belong_to_the_provider(self):
        wrong = dict(item("eporner"), page_url="https://other.example.com/video-Sample0001/")
        with self.assertRaises(adult_disc.AdultDiscoveryError):
            streams.streams_for(wrong)

    def test_pasted_item_builds_the_embed_url_from_the_page_not_the_hash(self):
        # ``adult_discovery.link`` gives a pasted URL a hashed provider id, so
        # the embed page must be derived from the page URL instead.
        pasted = {
            "provider": "eporner",
            "provider_id": "bf0fb23d858b0b79",
            "title": "Sample Adult Item",
            "page_url": "https://www.eporner.com/video-Sample0001/sample/",
        }
        markup = '<script>EP.video.player.url="https://gvideo.eporner.com/Sample0001/Sample0001.mp4";</script>'
        with patch.object(streams, "_fetch_html", return_value={"ok": True, "html": markup}) as fetch:
            result = streams.streams_for(pasted)
        self.assertEqual(fetch.call_args[0][0], "https://www.eporner.com/embed/Sample0001/")
        self.assertEqual(len(result["streams"]), 1)


class AdultStreamsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_module.app.test_client()

    def test_streams_route_returns_the_cached_item_and_streams(self):
        cached = item("eporner")
        with patch.object(app_module.adult_disc, "cached_item", return_value=cached), patch.object(
            app_module.adult_streams,
            "streams_for",
            return_value={"streams": [{"name": "Eporner · MP4"}], "warnings": [], "page_meta": {"description": "Provider synopsis."}, "cached": False},
        ):
            response = self.client.post(
                "/api/adult/streams", json={"provider": "eporner", "id": "Sample0001"}
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["streams"][0]["name"], "Eporner · MP4")
        self.assertEqual(body["page_meta"]["description"], "Provider synopsis.")

    def test_streams_route_reports_an_empty_cache(self):
        with patch.object(app_module.adult_disc, "cached_item", return_value=None):
            response = self.client.post(
                "/api/adult/streams", json={"provider": "eporner", "id": "missing"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])

    def test_download_status_filters_adult_rows_without_consuming(self):
        snapshot = {
            "active": [
                {"item_id": "1", "title": "Adult A", "category": "english_adult", "progress": 12},
                {"item_id": "2", "title": "Movie B", "category": "general_movie", "progress": 50},
                {"item_id": "3", "title": "Adult A", "category": "", "progress": 30},
            ],
            "syncing": [
                {"item_id": "4", "title": "Adult A", "category": "english_adult"},
            ],
            "newly_imported": [
                {"item_id": "5", "title": "Adult A", "category": "english_adult"},
            ],
        }
        with patch.object(
            app_module.TORBOX_MONITOR, "status", return_value=snapshot
        ) as status:
            response = self.client.get("/api/adult/download-status?title=Adult%20A")
        status.assert_called_once_with(consume_imported=False)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual({row["item_id"] for row in body["active"]}, {"1", "3"})
        self.assertEqual(body["active_count"], 3)
        self.assertEqual(len(body["imported"]), 1)

    def test_download_status_without_title_keeps_category_only(self):
        snapshot = {
            "active": [
                {"item_id": "1", "title": "Adult A", "category": "english_adult"},
                {"item_id": "2", "title": "Movie B", "category": "general_movie"},
            ],
            "syncing": [],
            "newly_imported": [],
        }
        with patch.object(app_module.TORBOX_MONITOR, "status", return_value=snapshot):
            body = self.client.get("/api/adult/download-status").get_json()
        self.assertEqual([row["item_id"] for row in body["active"]], ["1"])

    def test_local_status_keeps_only_adult_jobs_and_display_fields(self):
        import roxxy_download_engine as engine

        jobs = {
            "adult1": {
                "id": "adult1",
                "title": "Adult Job",
                "status": "downloading",
                "progress": 12.5,
                "engine": "yt-dlp",
                "url": "https://cdn.example.com/a.mp4",
                "output_dir": "C:/private",
                "pid": 42,
                "meta_snapshot": {"_roxxySource": "english_adult"},
            },
            "roxxy1": {
                "id": "roxxy1",
                "title": "Roxxy Job",
                "status": "downloading",
                "meta_snapshot": {"_roxxySource": "roxxy"},
            },
            "plain1": {"id": "plain1", "title": "No Source", "status": "completed"},
        }
        with patch.object(engine, "_jobs", jobs):
            body = self.client.get("/api/adult/local-status").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual([row["id"] for row in body["jobs"]], ["adult1"])
        for field in ("url", "output_dir", "pid", "meta_snapshot"):
            self.assertNotIn(field, body["jobs"][0])


if __name__ == "__main__":
    unittest.main()