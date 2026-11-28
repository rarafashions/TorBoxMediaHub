"""Release discovery for missing series episodes (TorBox cache + indexers + Roxxy)."""
import unittest
from unittest.mock import patch

import requests

import app as app_module
import roxxy_module as rm


HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40


def _torrent_row(title, episode, info_hash, seeds="12", leechers="3", size="1073741824"):
    return {
        "kind": "torrent",
        "source": "TPB",
        "indexer": "TPB",
        "title": title,
        "episode": episode,
        "hash": info_hash,
        "magnet": f"magnet:?xt=urn:btih:{info_hash}&dn=test",
        "nzb_url": "",
        "seeders": int(seeds),
        "leechers": int(leechers),
        "peers": int(seeds) + int(leechers),
        "size": int(size),
        "quality": "1080p",
        "tags": ["1080p"],
        "cached": False,
    }


def _usenet_row(title, episode):
    return {
        "kind": "usenet",
        "source": "NZBIndex",
        "indexer": "NZBIndex",
        "title": title,
        "episode": episode,
        "hash": "d" * 32,
        "magnet": "",
        "nzb_url": "https://nzbindex.example/1.nzb",
        "seeders": None,
        "leechers": None,
        "peers": None,
        "size": 700 * 1024 ** 2,
        "quality": "1080p",
        "tags": ["1080p"],
        "cached": False,
    }


class SizeParsingTests(unittest.TestCase):
    def test_plain_bytes(self):
        self.assertEqual(app_module._parse_size_bytes("1073741824"), 1073741824)

    def test_decimal_units(self):
        self.assertEqual(app_module._parse_size_bytes("1.5 GiB"), int(1.5 * 1024 ** 3))
        self.assertEqual(app_module._parse_size_bytes("700 MB"), 700 * 1024 ** 2)

    def test_rejects_junk(self):
        for value in ("", None, "unknown", "0"):
            self.assertIsNone(app_module._parse_size_bytes(value))


class CandidateBuildingTests(unittest.TestCase):
    def test_torrent_without_magnet_is_rejected(self):
        self.assertIsNone(app_module._episode_release_candidate("torrent", "TPB", "Show S01E01"))

    def test_usenet_requires_nzb_url(self):
        self.assertIsNone(app_module._episode_release_candidate("usenet", "NZBIndex", "Show S01E01"))

    def test_seed_and_leech_counts_shape_peers(self):
        row = app_module._episode_release_candidate(
            "torrent", "TPB", "Show S01E01 1080p", 1,
            magnet=f"magnet:?xt=urn:btih:{HASH_A}", seeders="9", leechers="4",
        )
        self.assertEqual(row["seeders"], 9)
        self.assertEqual(row["leechers"], 4)
        self.assertEqual(row["peers"], 13)
        self.assertEqual(row["quality"], "1080p")


class EpisodeSearchEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.settings = {
            "torbox_api_key": "torbox-key",
            "tmdb_api_key": "tmdb-key",
            "tpdb_api_key": "",
            "nzbgeek_api_key": "",
        }

    def _post(self, payload):
        with patch.object(app_module.db, "get_settings", return_value=self.settings):
            return self.client.post("/api/series/episode-search", json=payload)

    def test_rejects_empty_episode_list(self):
        response = self._post({"series_title": "Dark", "category": "tv_show", "season": 1, "episodes": []})
        self.assertEqual(response.status_code, 400)

    def test_rejects_unsupported_category(self):
        response = self._post({"series_title": "Dark", "category": "english_adult", "season": 1, "episodes": [1]})
        self.assertEqual(response.status_code, 400)

    def test_groups_results_per_episode_and_marks_cached(self):
        rows_ep1 = [_torrent_row("Dark S01E01 1080p", 1, HASH_A), _torrent_row("Dark S01E01 720p", 1, HASH_B)]
        rows_ep2 = [_torrent_row("Dark S01E02 1080p", 2, HASH_C)]
        with patch.object(app_module, "_tvmaze_series_title", return_value=("Dark", "tvmaze")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", side_effect=lambda q, s, e: rows_ep1 if e == 1 else rows_ep2), \
                patch.object(app_module, "_nzbindex_episode_candidates", side_effect=lambda q, s, e: [_usenet_row(f"Dark S01E0{e}", e)]), \
                patch.object(app_module, "_check_torbox_cached_hashes", return_value={HASH_A}), \
                patch.object(app_module, "_get_torbox_user_info", return_value={"is_pro": False, "plan": 1}):
            response = self._post({"series_title": "Dark", "category": "tv_show", "season": 1, "episodes": [1, 2]})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["query_title"], "Dark")
        self.assertEqual([row["episode"] for row in payload["episodes"]], [1, 2])
        ep1 = payload["episodes"][0]["candidates"]
        self.assertEqual(ep1[0]["hash"], HASH_A, "cached row must sort first")
        self.assertTrue(ep1[0]["cached"])
        self.assertFalse(ep1[1]["cached"])
        self.assertEqual(payload["cached_count"], 1)
        usenet = [c for c in payload["candidates"] if c["kind"] == "usenet"]
        self.assertEqual(len(usenet), 2)
        self.assertEqual(usenet[0]["nzb_url"], "https://nzbindex.example/1.nzb")

    def test_nzbgeek_is_reported_as_needing_premium_key(self):
        with patch.object(app_module, "_tvmaze_series_title", return_value=("Dark", "library")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nzbindex_episode_candidates", return_value=[]), \
                patch.object(app_module, "_get_torbox_user_info", return_value={"is_pro": False, "plan": 0}):
            response = self._post({"series_title": "Dark", "category": "tv_show", "season": 1, "episodes": [1]})

        payload = response.get_json()
        nzbgeek = next(s for s in payload["sources"] if s["key"] == "nzbgeek")
        self.assertEqual(nzbgeek["state"], "unconfigured")
        self.assertIn("premium", nzbgeek["note"])
        self.assertTrue(any("NZBGeek" in note for note in payload["notes"]))
        roxxy = next(s for s in payload["sources"] if s["key"] == "roxxy")
        self.assertEqual(roxxy["state"], "skipped")

    def test_anime_uses_roxxy_candidates(self):
        roxxy_payload = {
            "ok": True,
            "catalogs": 2,
            "metas": 1,
            "direct_streams": 3,
            "candidates": [{
                "kind": "torrent",
                "source": "Roxxy Donghua",
                "indexer": "Animexin — Update Terbaru",
                "title": "Solo Leveling E01 1080p",
                "episode": 1,
                "magnet": f"magnet:?xt=urn:btih:{HASH_B}&dn=test",
                "nzb_url": "",
                "quality": "1080p",
            }],
            "errors": [],
        }
        with patch.object(app_module, "_tvmaze_series_title", return_value=("Solo Leveling", "library")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nzbindex_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nyaa_episode_candidates", return_value=[]), \
                patch.object(app_module, "_check_torbox_cached_hashes", return_value=set()), \
                patch.object(app_module, "_get_torbox_user_info", return_value={"is_pro": True, "plan": 2}), \
                patch.object(rm, "search_episode_releases", return_value=roxxy_payload) as roxxy:
            response = self._post({"series_title": "Solo Leveling", "category": "anime", "season": 1, "episodes": [1]})

        payload = response.get_json()
        roxxy.assert_called_once()
        self.assertEqual(roxxy.call_args.args[0], "Solo Leveling")
        sources = {s["key"]: s for s in payload["sources"]}
        self.assertEqual(sources["roxxy"]["state"], "ok")
        self.assertIn("direct", sources["roxxy"]["note"])
        self.assertEqual(payload["roxxy"]["metas"], 1)
        self.assertTrue(any(c["source"] == "Roxxy Donghua" for c in payload["candidates"]))

    def test_episode_cap_marks_truncation(self):
        with patch.object(app_module, "_tvmaze_series_title", return_value=("Dark", "library")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nzbindex_episode_candidates", return_value=[]), \
                patch.object(app_module, "_get_torbox_user_info", return_value={"is_pro": False, "plan": 0}):
            response = self._post({
                "series_title": "Dark",
                "category": "tv_show",
                "season": 1,
                "episodes": list(range(1, 31)),
            })
        payload = response.get_json()
        self.assertTrue(payload["truncated"])
        self.assertEqual(len(payload["episodes"]), app_module._EPISODE_SEARCH_MAX_EPISODES)


class RoxxySearchHelperTests(unittest.TestCase):
    def test_title_matching_keeps_the_same_show(self):
        self.assertTrue(rm._title_matches("Solo Leveling", "Solo Leveling Season 2"))
        self.assertFalse(rm._title_matches("Solo Leveling", "Sword Art Online"))

    def test_title_variants_drop_stream_suffixes(self):
        variants = rm._search_title_variants("Battle Through The Heavens NF")
        self.assertEqual(variants[0], "Battle Through The Heavens NF")
        self.assertIn("Battle Through The Heavens", variants)
        self.assertTrue(rm._title_matches("Battle Through The Heavens", "Battle Through The Heavens Season 5"))
        self.assertEqual(rm._search_title_variants("Solo Leveling"), ["Solo Leveling"])

    def test_search_catalogs_need_a_search_extra(self):
        self.assertTrue(rm._catalog_supports_search({"extra": [{"name": "search"}]}))
        self.assertTrue(rm._catalog_supports_search({"extra": {"name": "search"}}))
        self.assertFalse(rm._catalog_supports_search({"extra": [{"name": "skip"}]}))

    def test_missing_manifest_is_reported_without_network(self):
        with patch.object(rm, "_manifest_url", return_value=""):
            result = rm.search_episode_releases("Solo Leveling", 1, [1])
        self.assertFalse(result["ok"])
        self.assertIn("Manifest", result["error"])


class _FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")
        return None

    def json(self):
        return self._payload


NYAA_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel>
<item>
  <title>[SubsPlease] Dark - 01 (1080p) [ABCD1234].mkv</title>
  <link>https://nyaa.si/view/1</link>
  <nyaa:seeders>31</nyaa:seeders>
  <nyaa:leechers>4</nyaa:leechers>
  <nyaa:size>1.4 GiB</nyaa:size>
  <nyaa:infoHash>1111111111111111111111111111111111111111</nyaa:infoHash>
</item>
</channel></rss>"""

NZBINDEX_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/" version="2.0"><channel>
<item>
  <title>Dark S01E01 1080p WEB-DL</title>
  <link>https://nzbindex.example/detail/9</link>
  <enclosure url="https://nzbindex.example/download/9.nzb" length="734003200" type="application/x-nzb"/>
  <newznab:attr name="size" value="734003200"/>
</item>
</channel></rss>"""


class IndexerParserTests(unittest.TestCase):
    """Parsers must tolerate real-world payload shapes without network access."""

    def test_tpb_rows_carry_seed_leech_and_size(self):
        rows = [{
            "id": "1",
            "name": "Dark S01E01 1080p WEB-DL",
            "info_hash": HASH_A,
            "seeders": "18",
            "leechers": "2",
            "size": "1610612736",
        }]
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(rows)):
            found = app_module._tpb_episode_candidates("Dark", 1, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["seeders"], 18)
        self.assertEqual(found[0]["leechers"], 2)
        self.assertEqual(found[0]["peers"], 20)
        self.assertEqual(found[0]["size"], 1610612736)
        self.assertEqual(found[0]["episode"], 1)
        self.assertIn(HASH_A, found[0]["magnet"])

    def test_tpb_no_results_marker_is_ignored(self):
        rows = [{"id": "0", "name": "No results returned", "info_hash": "0" * 40}]
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(rows)):
            self.assertEqual(app_module._tpb_episode_candidates("Dark", 1, 1), [])

    def test_nyaa_rss_reads_seeders_and_size(self):
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(text=NYAA_RSS)):
            found = app_module._nyaa_episode_candidates("Dark", 1, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "Nyaa")
        self.assertEqual(found[0]["seeders"], 31)
        self.assertEqual(found[0]["leechers"], 4)
        self.assertEqual(found[0]["size"], int(1.4 * 1024 ** 3))
        self.assertEqual(found[0]["episode"], 1)

    def test_nzbindex_rss_keeps_nzb_url_and_size(self):
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(text=NZBINDEX_RSS)):
            found = app_module._nzbindex_episode_candidates("Dark", 1, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "usenet")
        self.assertEqual(found[0]["nzb_url"], "https://nzbindex.example/download/9.nzb")
        self.assertEqual(found[0]["size"], 734003200)
        self.assertEqual(found[0]["episode"], 1)

    def test_nzbgeek_reads_channel_items_and_size_attr(self):
        payload = {"channel": {"item": [{
            "title": "Dark S01E01 1080p",
            "link": "https://nzbgeek.example/get/1.nzb",
            "attr": [{"name": "size", "value": "1073741824"}],
        }]}}
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(payload)):
            found = app_module._nzbgeek_episode_candidates("Dark", 1, 1, "key")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "NZBGeek")
        self.assertEqual(found[0]["size"], 1073741824)
        self.assertEqual(found[0]["nzb_url"], "https://nzbgeek.example/get/1.nzb")

    def test_eztv_filters_season_and_episode(self):
        payload = {"torrents": [
            {"title": "Dark S01E01 1080p", "hash": HASH_A, "season": "1", "episode": "1", "seeds": "10", "peers": "14", "size_bytes": "1073741824"},
            {"title": "Dark S02E01 1080p", "hash": HASH_B, "season": "2", "episode": "1", "seeds": "5", "peers": "6"},
        ]}
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(payload)):
            found = app_module._eztv_episode_candidates("tt1234567", 1, [1])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Dark S01E01 1080p")
        self.assertEqual(found[0]["leechers"], 4)

    def test_torrentio_reads_seed_and_size_from_title_metadata(self):
        payload = {"streams": [{
            "infoHash": HASH_C,
            "title": "Dark.S01E01.1080p 👤 25 💾 1.5 GB ⚙️ ThePirateBay",
        }]}
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(payload)):
            found = app_module._torrentio_episode_candidates("tt1234567", 1, 1)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["seeders"], 25)
        self.assertEqual(found[0]["size"], int(1.5 * 1024 ** 3))


class EpisodeCacheStateTests(unittest.TestCase):
    def test_usenet_cache_state_is_unknown_without_nzb_hash(self):
        client = app_module.app.test_client()
        settings = {"torbox_api_key": "k", "tmdb_api_key": "", "nzbgeek_api_key": ""}
        with patch.object(app_module.db, "get_settings", return_value=settings), \
                patch.object(app_module, "_tvmaze_series_title", return_value=("Dark", "library")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nzbindex_episode_candidates", return_value=[_usenet_row("Dark S01E01", 1)]), \
                patch.object(app_module, "_check_torbox_cached_hashes", return_value=set()), \
                patch.object(app_module, "_get_torbox_user_info", return_value={"is_pro": True, "plan": 2}):
            response = client.post(
                "/api/series/episode-search",
                json={"series_title": "Dark", "category": "tv_show", "season": 1, "episodes": [1]},
            )
        payload = response.get_json()
        usenet = next(c for c in payload["candidates"] if c["kind"] == "usenet")
        self.assertIsNone(usenet["cached"])
        cache_source = next(s for s in payload["sources"] if s["key"] == "torbox_cache")
        self.assertIn("tidak dapat dipastikan", cache_source["note"])


class ReleaseMatchTests(unittest.TestCase):
    def test_rejects_other_show_that_contains_the_title(self):
        self.assertFalse(app_module._release_show_matches("Dark.Matter.2024.S01E01.WEB.x264", "Dark"))
        self.assertFalse(app_module._release_show_matches("The.Dark.Knight.2008.S01E01", "Dark"))

    def test_keeps_region_and_year_extras(self):
        self.assertTrue(app_module._release_show_matches("The.Office.US.S01E01.1080p", "The Office"))
        self.assertTrue(app_module._release_show_matches("One.Piece.2023.S01E01", "One Piece"))
        self.assertTrue(app_module._release_show_matches("Dating.Naked.UK.S02E05", "Dating Naked UK"))

    def test_ignores_bracket_groups_and_release_tail(self):
        self.assertTrue(app_module._release_show_matches("[SubsPlease] Dark - 01 (1080p) [ABCD].mkv", "Dark"))

    def test_tpb_drops_unrelated_show(self):
        rows = [
            {"name": "Dark S01E01 1080p", "info_hash": HASH_A, "seeders": "5", "leechers": "1", "size": "1000"},
            {"name": "Dark Matter 2024 S01E01 1080p", "info_hash": HASH_B, "seeders": "9", "leechers": "2", "size": "2000"},
        ]
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(rows)):
            found = app_module._tpb_episode_candidates("Dark", 1, 1)
        self.assertEqual([row["hash"] for row in found], [HASH_A])

    def test_nzbindex_drops_unrelated_show(self):
        rss = NZBINDEX_RSS.replace("Dark S01E01", "Dark Matter 2024 S01E01")
        with patch.object(app_module.meta_eng.HTTP_SESSION, "get", return_value=_FakeResponse(text=rss)):
            self.assertEqual(app_module._nzbindex_episode_candidates("Dark", 1, 1), [])


class ErrorSanitizationTests(unittest.TestCase):
    def test_sanitizer_drops_query_string_and_api_key(self):
        text = (
            "451 Client Error for url: "
            "https://api.nzbgeek.info/api?t=search&q=Dark&apikey=SECRET123&o=json"
        )
        cleaned = app_module._sanitize_error_text(text)
        self.assertIn("api.nzbgeek.info/api", cleaned)
        self.assertNotIn("SECRET123", cleaned)
        self.assertNotIn("?", cleaned)
        self.assertNotIn("apikey", cleaned.lower())

    def test_key_value_pairs_are_masked_even_without_url(self):
        cleaned = app_module._sanitize_error_text("failed apikey=abc token: nope")
        self.assertNotIn("abc", cleaned)
        self.assertIn("apikey=***", cleaned)

    def test_endpoint_reports_sanitized_indexer_error(self):
        client = app_module.app.test_client()
        settings = {"torbox_api_key": "", "tmdb_api_key": "", "nzbgeek_api_key": "sekret"}
        with patch.object(app_module.db, "get_settings", return_value=settings), \
                patch.object(app_module, "_tvmaze_series_title", return_value=("Dark", "library")), \
                patch.object(app_module, "_resolve_imdb_id_from_tmdb", return_value=None), \
                patch.object(app_module, "_tpb_episode_candidates", return_value=[]), \
                patch.object(app_module, "_nzbindex_episode_candidates", return_value=[]), \
                patch.object(
                    app_module,
                    "_nzbgeek_episode_candidates",
                    side_effect=RuntimeError(
                        "500 Server Error for url: https://api.nzbgeek.info/api?apikey=sekret"
                    ),
                ):
            response = client.post(
                "/api/series/episode-search",
                json={"series_title": "Dark", "category": "tv_show", "season": 1, "episodes": [1]},
            )
        payload = response.get_json()
        nzbgeek = next(s for s in payload["sources"] if s["key"] == "nzbgeek")
        self.assertEqual(nzbgeek["state"], "error")
        self.assertNotIn("sekret", nzbgeek["error"])
        self.assertNotIn("sekret", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()