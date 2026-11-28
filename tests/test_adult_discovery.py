"""Offline tests for the English Adult discovery foundation (phase A1).

Every fixture is synthetic: titles, ids and thumbnails are placeholders so the
repository never carries provider content. No test touches the network.
"""

import json
import time
import unittest
from unittest.mock import patch

import adult_discovery as adult_disc
import adult_providers as adult_providers
import app as app_module


class FakeResponse:
    def __init__(self, status=200, payload=None, text=None, content_type="application/json", url=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self._text = text if text is not None else json.dumps(self._payload)
        self.content = self._text.encode("utf-8") if self._payload else b""
        self.headers = {"Content-Type": content_type}
        self.encoding = "utf-8"
        self.url = url
        self.closed = False

    def iter_content(self, chunk_size=65536):
        yield self._text.encode("utf-8")

    def close(self):
        self.closed = True

    def json(self):
        return self._payload


def eporner_payload(videos, total_pages=5):
    return {"total_pages": total_pages, "videos": videos}


def eporner_item(item_id="SampleId0001", url=None, **overrides):
    item = {
        "id": item_id,
        "title": "Sample Eporner Item",
        "views": 1021368,
        "rate": "4.6",
        "url": url or f"https://www.eporner.com/video-{item_id}/sample-item/",
        "added": "2026-11-27 12:34:56",
        "length_sec": 770,
        "default_thumb": {"src": "https://static-sg-cdn.eporner.com/sample.jpg"},
        "keywords": "sample, placeholder",
    }
    item.update(overrides)
    return item


def pornhub_payload(videos):
    return {"videos": videos}


def pornhub_item(video_id="sample0000001", **overrides):
    item = {
        "video_id": video_id,
        "title": "Sample Pornhub Item",
        "url": f"https://www.pornhub.com/view_video.php?viewkey={video_id}",
        "default_thumb": "https://ei.phncdn.com/sample.jpg",
        "duration": "10:30",
        "views": 2826308,
        "rating": 90.4,
        "publish_date": "2026-11-20 10:00:00",
        "tags": [{"tag_name": "Sample Tag"}, {"tag_name": "Placeholder"}],
    }
    item.update(overrides)
    return item


class AdultDiscoveryTestBase(unittest.TestCase):
    def setUp(self):
        self._original_interval = adult_disc.MIN_REQUEST_INTERVAL_SECONDS
        adult_disc.MIN_REQUEST_INTERVAL_SECONDS = 0
        adult_disc.reset_state_for_tests()
        self.client = app_module.app.test_client()

    def tearDown(self):
        adult_disc.MIN_REQUEST_INTERVAL_SECONDS = self._original_interval
        adult_disc.reset_state_for_tests()

    def feed_html(self, provider, html, mode="popular", page=1):
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(status=200, text=html, content_type="text/html")
        ):
            return adult_disc.feed(provider, mode, page)


class EpornerTests(AdultDiscoveryTestBase):
    def test_popular_feed_normalises_rating_and_caches(self):
        payload = eporner_payload([eporner_item()])
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ) as mocked:
            first = adult_disc.feed("eporner", "popular", 1)
            second = adult_disc.feed("eporner", "popular", 1)

        self.assertEqual(mocked.call_count, 1)
        self.assertTrue(first["ok"])
        self.assertEqual(len(first["items"]), 1)
        item = first["items"][0]
        self.assertEqual(item["provider"], "eporner")
        self.assertEqual(item["duration_sec"], 770)
        self.assertEqual(item["rating"], 9.2)
        self.assertEqual(item["views"], 1021368)
        self.assertTrue(item["page_url"].startswith("https://www.eporner.com/video-"))
        self.assertTrue(item["thumb"].startswith("https://"))
        self.assertTrue(first["has_more"])

        params = mocked.call_args.kwargs["params"]
        self.assertEqual(params["order"], "top-weekly")
        self.assertEqual(params["query"], "")
        self.assertEqual(params["page"], 1)
        self.assertEqual(params["format"], "json")

        self.assertTrue(second["providers"][0]["cached"])
        self.assertEqual(second["providers"][0]["ms"], 0)

    def test_api_items_with_foreign_page_urls_are_dropped(self):
        payload = eporner_payload(
            [
                eporner_item(),
                eporner_item(item_id="Bad00000002", url="https://evil.example.com/video-x/"),
            ]
        )
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ):
            result = adult_disc.feed("eporner", "popular", 1)

        self.assertEqual(len(result["items"]), 1)
        codes = [warning["code"] for warning in result["warnings"]]
        self.assertIn("dropped_invalid", codes)
        self.assertFalse(any("evil.example.com" in message["message"] for message in result["warnings"]))


class PornhubTests(AdultDiscoveryTestBase):
    def test_search_uses_query_and_parses_clock(self):
        payload = pornhub_payload(
            [pornhub_item(duration="1:02:03", rating=95.74)]
        )
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ) as mocked:
            result = adult_disc.search("test query", "pornhub", 2)

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 3723)
        self.assertEqual(item["rating"], 9.6)
        self.assertEqual(item["tags"], ["Sample Tag", "Placeholder"])
        self.assertEqual(item["published"], "2026-11-20")
        self.assertFalse(result["has_more"])

        params = mocked.call_args.kwargs["params"]
        self.assertEqual(params["search"], "test query")
        self.assertEqual(params["page"], 2)
        self.assertNotIn("ordering", params)


class ProviderFailureTests(AdultDiscoveryTestBase):
    def test_cloudflare_403_opens_breaker_and_stops_repeat_calls(self):
        cloudflare = FakeResponse(
            status=403,
            text="<!DOCTYPE html><html><title>Just a moment...</title></html>",
            content_type="text/html",
        )
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=cloudflare) as mocked:
            first = adult_disc.feed("pornhub", "popular", 1)
            second = adult_disc.feed("pornhub", "popular", 1)

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(first["warnings"][0]["code"], "blocked")
        self.assertEqual(first["providers"][0]["status"], "blocked")
        self.assertEqual(second["warnings"][0]["code"], "cooldown")
        self.assertEqual(second["providers"][0]["status"], "cooldown")
        self.assertNotIn("Just a moment", second["warnings"][0]["message"])
        self.assertNotIn("http", second["warnings"][0]["message"].lower())

    def test_rate_limit_is_reported(self):
        limited = FakeResponse(status=429, text="{}")
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=limited):
            result = adult_disc.feed("eporner", "popular", 1)
        self.assertEqual(result["warnings"][0]["code"], "rate_limited")
        self.assertIn("membatasi", result["warnings"][0]["message"])

    def test_invalid_json_is_parse_error(self):
        broken = FakeResponse(status=200, text="<html>changed</html>", content_type="text/html")
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=broken):
            result = adult_disc.feed("eporner", "popular", 1)
        self.assertEqual(result["warnings"][0]["code"], "parse_error")
        self.assertIn("berubah", result["warnings"][0]["message"])

    def test_http_error_carries_status_only(self):
        failed = FakeResponse(status=500, text="{}")
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=failed):
            result = adult_disc.feed("pornhub", "popular", 1)
        self.assertEqual(result["warnings"][0]["code"], "http_error")
        self.assertIn("500", result["warnings"][0]["message"])


class AggregationTests(AdultDiscoveryTestBase):
    def test_fanout_keeps_partial_results(self):
        def fake_get(url, **kwargs):
            if "eporner" in url:
                return FakeResponse(payload=eporner_payload([eporner_item()], total_pages=1))
            return FakeResponse(status=500, text="{}")

        with patch.object(adult_disc.HTTP_SESSION, "get", side_effect=fake_get):
            result = adult_disc.feed("all", "popular", 1)

        self.assertEqual(len(result["items"]), 1)
        statuses = {row["id"]: row["status"] for row in result["providers"]}
        self.assertEqual(statuses["eporner"], "ok")
        self.assertEqual(statuses["pornhub"], "http_error")
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertIn("http_error", codes)

    def test_fanout_interleaves_providers(self):
        def fake_get(url, **kwargs):
            if "eporner" in url:
                return FakeResponse(
                    payload=eporner_payload(
                        [eporner_item("E1"), eporner_item("E2")], total_pages=1
                    )
                )
            return FakeResponse(
                payload=pornhub_payload([pornhub_item("P1"), pornhub_item("P2")])
            )

        with patch.object(adult_disc.HTTP_SESSION, "get", side_effect=fake_get):
            result = adult_disc.feed("all", "popular", 1)

        order = [item["provider"] for item in result["items"]]
        self.assertEqual(order[:4], ["eporner", "pornhub", "eporner", "pornhub"])


class ValidationTests(AdultDiscoveryTestBase):
    def test_short_query_is_rejected(self):
        with self.assertRaises(adult_disc.AdultDiscoveryError):
            adult_disc.search("a", "", 1)

    def test_unknown_provider_feed_is_rejected(self):
        with self.assertRaises(adult_disc.AdultDiscoveryError):
            adult_disc.feed("nope", "popular", 1)

    def test_disabled_provider_feed_is_rejected(self):
        with self.assertRaises(adult_disc.AdultDiscoveryError):
            adult_disc.feed("spankbang", "popular", 1)

    def test_unsupported_mode_is_rejected(self):
        with self.assertRaises(adult_disc.AdultDiscoveryError):
            adult_disc.feed("xnxx", "new", 1)

    def test_unknown_provider_in_search_is_a_warning(self):
        payload = pornhub_payload([pornhub_item()])
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ):
            result = adult_disc.search("sample text", "pornhub,ghost", 1)
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertIn("unknown_provider", codes)

    def test_providers_payload_lists_all_known_sources(self):
        payload = adult_disc.providers_payload()
        self.assertTrue(payload["ok"])
        ids = [row["id"] for row in payload["providers"]]
        self.assertEqual(tuple(ids), adult_providers.PROVIDER_ORDER)
        by_id = {row["id"]: row for row in payload["providers"]}
        self.assertTrue(by_id["eporner"]["enabled"])
        self.assertTrue(by_id["pornhub"]["enabled"])
        self.assertTrue(by_id["xhamster"]["enabled"])
        self.assertEqual(by_id["spankbang"]["status"], "blocked")
        self.assertEqual(by_id["sunporno"]["status"], "search_only")
        self.assertEqual(len(payload["modes"]), 3)


class AdultEndpointTests(AdultDiscoveryTestBase):
    def test_providers_endpoint(self):
        response = self.client.get("/api/adult/providers")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(len(body["providers"]), 10)

    def test_feed_endpoint_happy_path(self):
        payload = eporner_payload([eporner_item()])
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ):
            response = self.client.get("/api/adult/feed?provider=eporner&mode=popular")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["mode"], "popular")
        self.assertEqual(len(body["items"]), 1)

    def test_feed_endpoint_rejects_unknown_provider(self):
        response = self.client.get("/api/adult/feed?provider=nope")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_feed_endpoint_rejects_unknown_mode(self):
        response = self.client.get("/api/adult/feed?provider=eporner&mode=bogus")
        self.assertEqual(response.status_code, 400)

    def test_search_endpoint_rejects_short_query(self):
        response = self.client.get("/api/adult/search?q=a")
        self.assertEqual(response.status_code, 400)
        self.assertIn("minimal", response.get_json()["error"])

    def test_search_endpoint_returns_results(self):
        payload = pornhub_payload([pornhub_item()])
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)
        ):
            response = self.client.get("/api/adult/search?q=sample&providers=pornhub")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["query"], "sample")
        self.assertEqual(len(body["items"]), 1)

    def test_mainstream_discovery_still_rejects_adult_category(self):
        response = self.client.get("/api/discover?category=english_adult&feed=trending")
        self.assertEqual(response.status_code, 400)


class HtmlProviderTests(AdultDiscoveryTestBase):
    """Parsers are exercised on synthetic markup; no provider content is stored."""

    XHAMSTER = """
    <div class="video-thumb" data-video-id="xh0001">
      <a class="video-thumb__image-container thumb-image-container" href="/videos/sample-one-xh0001">
        <img class="tnum-1 thumb-image-container__image" src="https://thumb-nss.xhcdn.com/sample1.jpg" alt="">
        <div class="thumb-image-container__duration">12:34</div>
      </a>
      <div class="video-thumb-info">
        <a class="video-thumb-info__name" href="/videos/sample-one-xh0001" title="Sample One">Sample One</a>
        <div class="video-thumb-views">1.2M views</div>
      </div>
    </div>
    """

    YOUJIZZ = """
    <div class="default video-item">
      <div class="frame-wrapper">
        <a class="frame video" data-video-id="129526361" href="/videos/sample-one-129526361.html">
          <div class="preload-line"></div>
          <img class="img-responsive lazy" src="https://cdn.youjizz.com/sample1.jpg" data-original="https://cdn.youjizz.com/sample1.jpg" alt="">
        </a>
      </div>
      <div class="video-title"><a class="agego-content-title" href="/videos/sample-one-129526361.html">Sample One</a></div>
      <div class="video-content-wrapper">
        <span class="time">12:34</span>
        <select class="video-bar-rating-view" data-value="4.2"><option>4</option></select>
        <span class="views"><span class="format-views">1.2M</span></span>
      </div>
    </div>
    """

    XVIDEOS = """
    <div class="thumb-block video" data-video="video12345678">
      <div class="thumb">
        <a class="thumb-link" href="/video12345678/sample_one">
          <img src="https://cdn77-pic.xvideos-cdn.com/sample1.jpg" data-src="https://cdn77-pic.xvideos-cdn.com/sample1.jpg" alt="">
        </a>
      </div>
      <div class="thumb-under">
        <div class="title" title="Sample One"><a href="/video12345678/sample_one">Sample One</a></div>
        <div class="video-metadata">
          <a class="name" href="/sample_channel">Sample Channel</a>
          <span class="views-count">1.2M</span><span class="views-text">views</span>
          <span class="duration-container"><span class="duration">12 min</span></span>
        </div>
      </div>
    </div>
    """

    XNXX = """
    <div class="thumb-block video" data-video="12345678">
      <div class="thumb">
        <a class="thumb-link" href="/video12345678/sample_one">
          <img data-src="https://cdn.xnxx-cdn.com/sample1.jpg" src="https://cdn.xnxx-cdn.com/sample1.jpg" alt="">
        </a>
      </div>
      <div class="thumb-under">
        <div class="uploader"><a class="name" href="/sample_channel">Sample Channel</a></div>
        <a class="title" href="/video12345678/sample_one" title="Sample One">Sample One</a>
        <div class="metadata">
          <span class="left">12min - 720p</span>
          <span class="right">1.2M 85%</span>
        </div>
      </div>
    </div>
    """

    YOUPORN = """
    <article class="video-box pc js_video-box" data-video-id="123456">
      <div class="preloadLine"></div>
      <a class="tm_video_link video-box-image" href="/watch/123456/sample-one/">
        <div class="thumb-image-container"><img class="thumb-image" data-src="https://cdn.youporn.com/sample1.jpg" src="https://cdn.youporn.com/sample1.jpg"></div>
        <div class="video-properties"><div class="video-duration">12:34</div></div>
      </a>
      <div class="thumb-info-wrapper">
        <div class="video-title-wrapper"><a class="video-title-text" href="/watch/123456/sample-one/">Sample One</a></div>
        <div class="info-views-container block"><span class="info-views">views: 1.2M</span><span class="info-views">rating: 85%</span></div>
      </div>
    </article>
    """

    XXXYMOVIES = """
    <div class="item">
      <a href="/sample/123456/sample-one/" title="Sample One">
        <img class="thumb lazy-load" src="https://xxxymovies.com/sample1.jpg" data-original="https://xxxymovies.com/sample1.jpg" alt="">
        <div class="info">
          <h3 class="infotitle"><span class="text_bold">Sample One</span></h3>
          <span class="item_rating">85%</span>
          <span class="added">Nov 27, 2026 | 1.2K views | 12:34</span>
        </div>
      </a>
    </div>
    """

    SUNPORNO = """
    <a class="item" href="/v/123456/sample-one/">
      <div class="img"><img class="thumb" data-webp="https://sunporno.com/sample1.jpg" src="https://sunporno.com/sample1.jpg" alt=""></div>
      <div class="duration">12:34</div>
      <div class="video-title">Sample One</div>
      <div class="video-views">1.2K views</div>
    </a>
    """

    XVIDEOS_FRAME = """
    <div class="frame-block thumb-block" data-id="92668009" data-eid="abc123">
      <div class="thumb-inside">
        <div class="thumb">
          <a href="/video.abc123/sample_two"><img src="https://cdn77-pic.xvideos-cdn.com/sample2.jpg" data-src="https://cdn77-pic.xvideos-cdn.com/sample2.jpg"></a>
          <p class="title"><a href="/video.abc123/sample_two" title="Sample Two">Sample Two</a></p>
          <span class="duration">10 min</span>
          <p class="metadata"><span class="bg"><a href="/sample_channel"><span class="name">Sample Channel</span></a> - 32.6k views - <span class="duration">10 min</span></span></p>
        </div>
      </div>
    </div>
    """

    XNXX_FRAME = """
    <div class="thumb-block with-uploader" data-id="35433055" data-eid="l3g0001">
      <div class="thumb-inside">
        <div class="thumb"><a href="/video-l3g0001/sample_two"><img src="https://cdn.xnxx-cdn.com/sample2.jpg" data-src="https://cdn.xnxx-cdn.com/sample2.jpg"></a></div>
        <div class="uploader"><a href="/sample_channel"><span class="name">Sample Channel</span></a></div>
      </div>
      <div class="thumb-under">
        <p>Sample Two</p>
        <a href="/video-l3g0001/sample_two" title="Sample Two">Sample Two</a>
        <p class="metadata"><span class="right">14.2M 99%</span> 14min - 720p</p>
      </div>
    </div>
    """

    def test_xhamster_card(self):
        result = self.feed_html("xhamster", self.XHAMSTER)
        self.assertTrue(result["ok"])
        item = result["items"][0]
        self.assertEqual(item["provider"], "xhamster")
        self.assertEqual(item["provider_id"], "xh0001")
        self.assertEqual(item["title"], "Sample One")
        self.assertEqual(item["duration_sec"], 754)
        self.assertEqual(item["views"], 1_200_000)
        self.assertTrue(item["page_url"].startswith("https://xhamster.com/videos/"))
        self.assertTrue(item["thumb"].startswith("https://"))
        self.assertEqual(result["providers"][0]["status"], "ok")

    def test_youjizz_card(self):
        result = self.feed_html("youjizz", self.YOUJIZZ)
        item = result["items"][0]
        self.assertEqual(item["provider_id"], "129526361")
        self.assertEqual(item["duration_sec"], 754)
        self.assertEqual(item["views"], 1_200_000)
        self.assertEqual(item["rating"], 8.4)
        self.assertTrue(item["page_url"].startswith("https://www.youjizz.com/videos/"))

    def test_xvideos_card(self):
        result = self.feed_html("xvideos", self.XVIDEOS)
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 720)
        self.assertEqual(item["views"], 1_200_000)
        self.assertTrue(item["page_url"].startswith("https://www.xvideos.com/video12345678/"))

    def test_xnxx_card(self):
        result = self.feed_html("xnxx", self.XNXX)
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 720)
        self.assertEqual(item["views"], 1_200_000)
        self.assertEqual(item["rating"], 8.5)

    def test_youporn_card(self):
        result = self.feed_html("youporn", self.YOUPORN)
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 754)
        self.assertEqual(item["views"], 1_200_000)
        self.assertEqual(item["rating"], 8.5)
        self.assertTrue(item["page_url"].startswith("https://www.youporn.com/watch/"))

    def test_xxxymovies_card(self):
        result = self.feed_html("xxxymovies", self.XXXYMOVIES, mode="new")
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 754)
        self.assertEqual(item["views"], 1200)
        self.assertEqual(item["rating"], 8.5)
        self.assertEqual(item["published"], "2026-11-27")
        self.assertTrue(item["page_url"].startswith("https://xxxymovies.com/sample/"))

    def test_sunporno_search_only(self):
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(status=200, text=self.SUNPORNO, content_type="text/html")
        ):
            result = adult_disc.search("sample", "sunporno", 1)
        item = result["items"][0]
        self.assertEqual(item["duration_sec"], 754)
        self.assertEqual(item["views"], 1200)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["providers"][0]["status"], "ok")

    def test_offhost_card_is_dropped_and_reported(self):
        html = self.XHAMSTER + """
        <div class="video-thumb" data-video-id="xh0002">
          <a class="video-thumb-info__name" href="https://evil.example.com/videos/x" title="Bad">Bad</a>
        </div>
        """
        result = self.feed_html("xhamster", html)
        self.assertEqual(len(result["items"]), 1)
        codes = [warning["code"] for warning in result["warnings"]]
        self.assertIn("dropped_invalid", codes)
        self.assertFalse(any("evil.example.com" in warning["message"] for warning in result["warnings"]))

    def test_feed_without_containers_is_parse_error(self):
        result = self.feed_html("xhamster", "<html><body><p>changed</p></body></html>")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["warnings"][0]["code"], "parse_error")

    def test_search_without_results_is_empty_not_error(self):
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(status=200, text="<html><body>no hits</body></html>", content_type="text/html")
        ):
            result = adult_disc.search("sample", "xhamster", 1)
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["warnings"], [])

    def test_page_two_uses_paged_url(self):
        with patch.object(
            adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(status=200, text=self.XHAMSTER, content_type="text/html")
        ) as mocked:
            adult_disc.feed("xhamster", "popular", 2)
        self.assertEqual(mocked.call_args.args[0], "https://xhamster.com/best/2")

    def test_xvideos_search_frame_layout(self):
        result = self.feed_html("xvideos", self.XVIDEOS_FRAME, mode="new")
        item = result["items"][0]
        self.assertEqual(item["title"], "Sample Two")
        self.assertEqual(item["duration_sec"], 600)
        self.assertEqual(item["views"], 32600)
        self.assertTrue(item["page_url"].startswith("https://www.xvideos.com/video.abc123/"))

    def test_xnxx_search_frame_layout(self):
        result = self.feed_html("xnxx", self.XNXX_FRAME)
        item = result["items"][0]
        self.assertEqual(item["title"], "Sample Two")
        self.assertEqual(item["duration_sec"], 840)
        self.assertEqual(item["views"], 14_200_000)
        self.assertEqual(item["rating"], 9.9)

    def test_view_suffix_is_not_treated_as_minutes(self):
        self.assertEqual(adult_disc._parse_duration_text("14.2M 99% 14min - 720p"), 840)
        self.assertEqual(adult_disc._parse_duration_text("32.6k views"), 0)
        self.assertEqual(adult_disc._parse_duration_text("1 hr 5 min"), 3900)

    def test_protocol_relative_thumb_is_upgraded(self):
        self.assertEqual(
            adult_disc._safe_thumb("//cdn.youjizz.com/a/b.jpg"),
            "https://cdn.youjizz.com/a/b.jpg",
        )
        self.assertEqual(adult_disc._safe_thumb("http://cdn.example.com/a.jpg"), "")

    def test_relative_published_text_is_dropped(self):
        self.assertEqual(adult_disc._published_from_text("15 hours ago | 0K views | 2:50"), "")
        self.assertEqual(adult_disc._published_from_text("Nov 27, 2026 | 1.2K views"), "2026-11-27")
        self.assertEqual(adult_disc._published_from_text("27 Nov 2026"), "2026-11-27")
        self.assertEqual(adult_disc._published_from_text("2026-11-27 10:00:00"), "2026-11-27")

    def test_single_page_search_stops_at_page_one(self):
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(status=200, text=self.SUNPORNO, content_type="text/html")) as mocked:
            first = adult_disc.search("sample", "sunporno", 1)
            second = adult_disc.search("sample", "sunporno", 2)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(second["items"], [])
        self.assertFalse(second["has_more"])

    def test_feed_url_builders(self):
        self.assertEqual(adult_disc._feed_url("xhamster", "popular", 1), "https://xhamster.com/best")
        self.assertEqual(adult_disc._feed_url("youjizz", "new", 3), "https://www.youjizz.com/newest-clips/3.html")
        self.assertEqual(adult_disc._feed_url("youporn", "top_rated", 2), "https://www.youporn.com/top_rated/?page=2")
        self.assertEqual(adult_disc._feed_url("xxxymovies", "new", 2), "https://xxxymovies.com/latest-updates/2/")
        self.assertIsNone(adult_disc._feed_url("sunporno", "popular", 1))

    def test_search_url_builders_escape_queries(self):
        self.assertEqual(adult_disc._search_url("xhamster", "sample text", 1), "https://xhamster.com/search/sample%20text")
        self.assertEqual(adult_disc._search_url("xvideos", "a&b", 2), "https://www.xvideos.com/?k=a%26b&p=2")
        self.assertEqual(adult_disc._search_url("xnxx", "sample", 3), "https://www.xnxx.com/search/sample/3")
        self.assertEqual(adult_disc._search_url("xxxymovies", "sample", 2), "https://xxxymovies.com/search/sample/")


class ManualLinkTests(AdultDiscoveryTestBase):
    def test_spankbang_link_becomes_one_item(self):
        result = adult_disc.link(
            "https://spankbang.com/8a2b9c/video/sample-two-title"
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["manual"])
        self.assertEqual(result["provider"]["id"], "spankbang")
        self.assertTrue(result["provider"]["manual"])
        item = result["item"]
        self.assertEqual(item["provider"], "spankbang")
        self.assertEqual(item["page_url"], "https://spankbang.com/8a2b9c/video/sample-two-title")
        self.assertEqual(item["title"], "Sample two title")
        self.assertEqual(item["source_kind"], "manual")
        self.assertEqual(item["thumb"], "")

    def test_http_is_upgraded_and_tracking_removed(self):
        result = adult_disc.link(
            "http://www.eporner.com/video-abc123/sample-item/?utm_source=feed&fbclid=abc#frag"
        )
        self.assertEqual(
            result["item"]["page_url"],
            "https://www.eporner.com/video-abc123/sample-item/",
        )

    def test_non_video_paths_are_rejected(self):
        for url in (
            "https://xhamster.com/best",
            "https://www.xnxx.com/search/sample",
            "https://www.xvideos.com/?k=sample",
            "https://xhamster.com/best/2?page=2",
        ):
            with self.subTest(url=url):
                with self.assertRaises(adult_disc.AdultDiscoveryError):
                    adult_disc.link(url)

    def test_unknown_host_and_bad_shape_are_rejected(self):
        for url in (
            "https://example.com/video/1",
            "javascript:alert(1)",
            "ftp://spankbang.com/1/video/sample",
            "https://user:pass@spankbang.com/1/video/sample",
            "https://spankbang.com:8443/1/video/sample",
            "https://spankbang.com/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(adult_disc.AdultDiscoveryError):
                    adult_disc.link(url)

    def test_id_only_path_falls_back_to_provider_label(self):
        result = adult_disc.link("https://www.youjizz.com/videos/123456.html")
        self.assertEqual(result["item"]["title"], "YouJizz · 123456")

    def test_query_style_page_uses_viewkey_as_title_hint(self):
        result = adult_disc.link("https://www.pornhub.com/view_video.php?viewkey=sample0000001")
        self.assertEqual(result["item"]["provider"], "pornhub")
        self.assertEqual(result["item"]["title"], "Pornhub · sample0000001")

    def test_route_returns_item_then_error(self):
        ok = self.client.post("/api/adult/link", json={
            "url": "https://spankbang.com/8a2b9c/video/sample-two-title",
        })
        self.assertEqual(ok.status_code, 200)
        body = ok.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["item"]["provider"], "spankbang")

        bad = self.client.post("/api/adult/link", json={"url": "https://example.com/video/1"})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.get_json()["ok"])

    def test_providers_payload_marks_manual_source(self):
        payload = adult_disc.providers_payload()
        manual = {row["id"] for row in payload["providers"] if row["manual"]}
        self.assertEqual(manual, {"spankbang"})
        self.assertEqual(payload["manual_providers"], ["spankbang"])
        statuses = {row["id"]: row["status"] for row in payload["providers"]}
        self.assertEqual(statuses["spankbang"], "blocked")
        self.assertEqual(statuses["sunporno"], "search_only")


class JsonAttributeTests(AdultDiscoveryTestBase):
    def test_json_data_video_attribute_yields_clean_id(self):
        self.assertEqual(
            adult_disc._attr_id('{"id":"1j1fu4db","isChannel":true}'), "1j1fu4db"
        )
        self.assertEqual(adult_disc._attr_id("35433055"), "35433055")
        self.assertEqual(adult_disc._attr_id("{not json"), "")
        self.assertEqual(adult_disc._attr_id(None), "")

    def test_xvideos_json_attribute_becomes_provider_id(self):
        html = """
        <div class="thumb-block" data-video='{"id":"uahevkkcc9f","isChannel":true}'>
          <a class="thumb-link" href="/video.uahevkkcc9f/sample-item"><img src="https://cdn77-pic.xvideos-cdn.com/a.jpg"></a>
          <div class="title"><a href="/video.uahevkkcc9f/sample-item" title="Sample Item">Sample Item</a></div>
          <span class="duration">5 min</span>
        </div>
        """
        result = self.feed_html("xvideos", html)
        self.assertEqual(result["items"][0]["provider_id"], "uahevkkcc9f")

    def test_xnxx_json_attribute_becomes_provider_id(self):
        html = """
        <div class="thumb-block" data-video='{"id":"1j1fu4db"}'>
          <a class="thumb-link" href="/video-1j1fu4db/sample-item"><img src="https://cdn.xnxx-cdn.com/a.jpg"></a>
          <a class="title" href="/video-1j1fu4db/sample-item" title="Sample Item">Sample Item</a>
          <p class="metadata"><span class="right">1.2M 90%</span> 12min - 720p</p>
        </div>
        """
        result = self.feed_html("xnxx", html)
        self.assertEqual(result["items"][0]["provider_id"], "1j1fu4db")

    def test_login_wall_is_reported_as_blocked(self):
        wall = FakeResponse(
            status=200,
            text="<html><head><title>YouPorn - Login</title></head><body>log in</body></html>",
            content_type="text/html",
            url="https://www.youporn.com/login/",
        )
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=wall):
            payload = adult_disc.feed("youporn", "popular", 1)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["providers"][0]["status"], "blocked")
        self.assertEqual(payload["warnings"][0]["code"], "blocked")

    def test_embedded_login_form_in_normal_page_is_not_a_wall(self):
        page = FakeResponse(
            status=200,
            text=(
                "<html><head><title>Sample Tube</title></head><body>"
                "<form id=\"loginform\"><input name=\"username\"><input name=\"password\"></form>"
                "<article class=\"video-box\"></article></body></html>"
            ),
            content_type="text/html",
            url="https://www.youporn.com/most_viewed/",
        )
        self.assertFalse(
            adult_disc._is_login_wall(page.url, page._text)
        )


class CachedItemTests(AdultDiscoveryTestBase):
    def test_cached_item_found_by_id_and_url(self):
        payload = eporner_payload([eporner_item()])
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)):
            adult_disc.feed("eporner", "popular", 1)
        by_id = adult_disc.cached_item("eporner", "SampleId0001")
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id["title"], "Sample Eporner Item")
        by_url = adult_disc.cached_item("eporner", "", by_id["page_url"])
        self.assertEqual(by_url["provider_id"], "SampleId0001")
        self.assertIsNone(adult_disc.cached_item("eporner", "other-id"))
        self.assertIsNone(adult_disc.cached_item("pornhub", "SampleId0001"))
        self.assertIsNone(adult_disc.cached_item("", "SampleId0001"))

    def test_expired_cache_entry_is_ignored(self):
        payload = eporner_payload([eporner_item()])
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)):
            adult_disc.feed("eporner", "popular", 1)
        for key, (_stamp, value) in list(adult_disc._CACHE.items()):
            adult_disc._CACHE[key] = (0.0, value)
        self.assertIsNone(adult_disc.cached_item("eporner", "SampleId0001"))


class AdultDetailTests(AdultDiscoveryTestBase):
    def setUp(self):
        super().setUp()
        app_module._ADULT_DETAIL_CACHE.clear()

    def test_manual_url_detail_reports_tpdb_not_configured(self):
        with patch.object(app_module.db, "get_settings", return_value={}), patch.object(
            app_module.db, "query_media_items", return_value=[]
        ):
            response = self.client.get(
                "/api/adult/detail",
                query_string={"url": "https://spankbang.com/8a2b9c/video/sample-two-title"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["item"]["provider"], "spankbang")
        self.assertEqual(body["tpdb"]["status"], "not_configured")
        self.assertEqual(body["in_library"], [])

    def test_cached_detail_uses_tpdb_once(self):
        payload = eporner_payload([eporner_item()])
        with patch.object(adult_disc.HTTP_SESSION, "get", return_value=FakeResponse(payload=payload)):
            adult_disc.feed("eporner", "popular", 1)
        tpdb_meta = {
            "title": "Sample Eporner Item",
            "year": 2026,
            "studio": "Sample Studio",
            "poster_url": "https://cdn.example.com/p.jpg",
            "plot": "Sample plot",
            "actresses": ["Sample Performer"],
            "genres": ["Sample Tag"],
            "rating": 8.1,
            "release_date": "2026-11-27",
            "runtime": 20,
        }
        with patch.object(app_module.db, "get_settings", return_value={"tpdb_api_key": "tpdb-test"}), patch.object(
            app_module.db, "query_media_items", return_value=[]
        ), patch.object(
            app_module.meta_eng, "_fetch_tpdb_metadata", return_value=tpdb_meta
        ) as mocked_tpdb:
            first = self.client.get("/api/adult/detail", query_string={"provider": "eporner", "id": "SampleId0001"})
            second = self.client.get("/api/adult/detail", query_string={"provider": "eporner", "id": "SampleId0001"})

        self.assertEqual(first.status_code, 200)
        body = first.get_json()
        self.assertEqual(body["tpdb"]["status"], "found")
        self.assertEqual(body["tpdb"]["studio"], "Sample Studio")
        self.assertEqual(body["item"]["title"], "Sample Eporner Item")
        self.assertTrue(second.get_json()["tpdb"]["cached"])
        self.assertEqual(mocked_tpdb.call_count, 1)

    def test_placeholder_title_skips_tpdb(self):
        with patch.object(app_module.db, "get_settings", return_value={"tpdb_api_key": "tpdb-test"}), patch.object(
            app_module.db, "query_media_items", return_value=[]
        ), patch.object(app_module.meta_eng, "_fetch_tpdb_metadata") as mocked_tpdb:
            response = self.client.get(
                "/api/adult/detail",
                query_string={"url": "https://www.youjizz.com/videos/123456.html"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["tpdb"]["status"], "skipped")
        mocked_tpdb.assert_not_called()

    def test_unknown_cached_item_is_404(self):
        response = self.client.get(
            "/api/adult/detail", query_string={"provider": "eporner", "id": "missing"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.get_json()["ok"])


class AdultWebdlTests(AdultDiscoveryTestBase):
    def setUp(self):
        super().setUp()
        app_module._WEB_DL_SUBMIT_TIMES.clear()
        app_module._WEB_DL_RECENT_LINKS.clear()

    def tearDown(self):
        super().tearDown()
        app_module._WEB_DL_SUBMIT_TIMES.clear()
        app_module._WEB_DL_RECENT_LINKS.clear()

    def test_adult_submit_uses_page_url_and_english_adult_category(self):
        posted = {}

        def fake_post(url, headers=None, files=None, timeout=None):
            posted["files"] = files
            return FakeResponse(
                payload={"success": True, "data": {"webdownload_id": 4242}}
            )

        with patch.object(
            app_module.db, "get_settings", return_value={"torbox_api_key": "test-key"}
        ), patch.object(
            app_module.meta_eng.HTTP_SESSION, "post", side_effect=fake_post
        ), patch.object(app_module.TORBOX_MONITOR, "track") as track:
            response = self.client.post(
                "/api/adult/webdl",
                json={
                    "url": "https://spankbang.com/8a2b9c/video/sample-two-title",
                    "title": "Sample Two Title",
                    "poster_url": "https://cdn.example.com/p.jpg",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["tracked_id"], "4242")
        self.assertEqual(body["provider"], "spankbang")
        self.assertEqual(
            posted["files"]["link"][1],
            "https://spankbang.com/8a2b9c/video/sample-two-title",
        )
        self.assertEqual(posted["files"]["name"][1], "Sample Two Title")
        self.assertEqual(track.call_args.kwargs["category"], "english_adult")
        self.assertEqual(track.call_args.kwargs["poster_url"], "https://cdn.example.com/p.jpg")
        self.assertEqual(track.call_args.kwargs["metadata"]["name"], "Sample Two Title")
        self.assertEqual(track.call_args.kwargs["metadata"]["poster"], "https://cdn.example.com/p.jpg")

    def test_adult_submit_rejects_listing_or_foreign_url(self):
        with patch.object(
            app_module.db, "get_settings", return_value={"torbox_api_key": "test-key"}
        ), patch.object(app_module.meta_eng.HTTP_SESSION, "post") as mocked_post:
            for url in (
                "https://xhamster.com/best",
                "https://example.com/video/1",
                "",
            ):
                with self.subTest(url=url):
                    response = self.client.post("/api/adult/webdl", json={"url": url})
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(response.get_json()["ok"])
        mocked_post.assert_not_called()

    def test_adult_submit_keeps_duplicate_guard(self):
        url = "https://spankbang.com/8a2b9c/video/sample-two-title"
        app_module._WEB_DL_RECENT_LINKS[url] = time.time()
        with patch.object(
            app_module.db, "get_settings", return_value={"torbox_api_key": "test-key"}
        ), patch.object(app_module.meta_eng.HTTP_SESSION, "post") as mocked_post:
            response = self.client.post("/api/adult/webdl", json={"url": url})
        self.assertEqual(response.status_code, 429)
        self.assertIn("baru saja dikirim", response.get_json()["error"])
        mocked_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()