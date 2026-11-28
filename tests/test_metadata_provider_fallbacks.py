import unittest
from unittest.mock import patch

import metadata_engine as meta


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class MovieTitleNormalizationTests(unittest.TestCase):
    def test_release_tags_and_language_suffixes_are_removed(self):
        value = meta.clean_search_title(
            "Spider-Man 9 Zbrusu nový den (2026) UHDRDV cz sk en.mkv"
        )
        self.assertEqual(value, "Spider-Man 9 Zbrusu nový den")

    def test_release_noise_tail_is_offered_as_fallback_candidate(self):
        candidates = meta._build_tmdb_search_candidates("Apocalypse Z New World HQ", 2026)
        self.assertEqual(candidates[0], "Apocalypse Z New World HQ")
        self.assertIn("Apocalypse Z New World", candidates)

        candidates = meta._build_tmdb_search_candidates(
            "Masters Of The Universe RETAIL DKSUBS", 2026
        )
        self.assertIn("Masters Of The Universe", candidates)

    def test_trailing_language_label_is_offered_as_fallback_candidate(self):
        candidates = meta._build_tmdb_search_candidates("Barfi Hindi", 2012)
        self.assertIn("Barfi", candidates)

    def test_embedded_subtitle_blurb_is_trimmed_from_fallback_candidate(self):
        candidates = meta._build_tmdb_search_candidates(
            "Swallowed Star Movie 2: Battle of the Primordial Star Subtitle Indonesia Sub Bagus"
        )
        self.assertIn("Swallowed Star Movie 2: Battle of the Primordial Star", candidates)

    def test_search_titles_prefer_parsed_title_then_parent_alias(self):
        titles = meta._search_title_candidates(
            "Il Signore degli Anelli - Gli Anelli del Potere",
            ("The Lord of the Rings The Rings of Power",),
        )
        self.assertEqual(titles[0], "Il Signore degli Anelli - Gli Anelli del Potere")
        self.assertIn("The Lord of the Rings The Rings of Power", titles)

    def test_localized_spiderman_title_generates_canonical_tmdb_candidate(self):
        candidates = meta._build_tmdb_search_candidates(
            "Spider-Man 9 Zbrusu nový den UHDRDV cz sk en", 2026
        )
        self.assertIn("Spider-Man: Brand New Day", candidates)
        self.assertIn("Spider-Man", candidates)

    @patch.object(meta.HTTP_SESSION, "get")
    def test_tmdb_retries_clean_variant_when_release_suffix_breaks_exact_query(self, mock_get):
        def fake_get(url, params=None, **kwargs):
            params = params or {}
            if url.endswith("/search/movie"):
                if params.get("query") == "Apocalypse Z New World":
                    return FakeResponse({"results": [{
                        "id": 1507696, "title": "Apocalypse Z: New World",
                        "original_title": "Apocalipsis Z: Nuevo mundo",
                        "release_date": "2026-10-09", "poster_path": "/apocalypse.jpg",
                        "genre_ids": [27], "original_language": "es",
                    }]})
                return FakeResponse({"results": []})
            if url.endswith("/search/tv"):
                return FakeResponse({"results": []})
            if url.endswith("/movie/1507696"):
                return FakeResponse({
                    "id": 1507696, "title": "Apocalypse Z: New World",
                    "original_title": "Apocalipsis Z: Nuevo mundo",
                    "release_date": "2026-10-09", "poster_path": "/apocalypse.jpg",
                    "runtime": 118, "genres": [{"id": 27, "name": "Horror"}],
                    "production_companies": [], "credits": {"cast": [], "crew": []},
                    "overview": "A virus turns people into the infected.",
                })
            raise AssertionError(f"Unexpected URL: {url}")

        mock_get.side_effect = fake_get
        result = meta._fetch_tmdb_metadata("Apocalypse Z New World HQ", "test-key", year=2026)
        self.assertIsNotNone(result)
        self.assertEqual(result["tmdb_id"], 1507696)
        self.assertEqual(result["_lookup"]["query"], "Apocalypse Z New World")

    @patch.object(meta, "download_poster_image_result", return_value={"local_poster_path": None, "poster_status": "missing", "poster_error": "No URL"})
    @patch.object(meta, "_fetch_tmdb_metadata")
    def test_localized_episode_uses_parent_folder_alias_when_title_fails(self, mock_tmdb, _poster):
        def fake_lookup(query, api_key, year=None, prefer_tv=False, diagnostics=None):
            if "Rings of Power" in query:
                return {
                    "title": "The Lord of the Rings: The Rings of Power",
                    "series_title": "The Lord of the Rings: The Rings of Power",
                    "year": 2022, "content_category": "tv_show", "has_metadata": 1,
                    "metadata_source": "tmdb", "poster_url": "https://cdn.example/rings.jpg",
                    "tmdb_id": 84773,
                }
            return None

        mock_tmdb.side_effect = fake_lookup
        item = {
            "unique_key": "lotr-s03e01",
            "file_name": "Il.Signore.degli.Anelli.-.Gli.Anelli.del.Potere.S03E01.ITA.ENG.2160p.mkv",
            "parent_name": "Il Signore degli Anelli - The Lord of the Rings The Rings of Power",
            "title": "Il Signore degli Anelli - Gli Anelli del Potere",
            "series_title": "Il Signore degli Anelli - Gli Anelli del Potere",
            "content_category": "tv_show", "season_num": 3, "episode_num": 1,
        }
        result = meta.enrich_media_item(item, {"tmdb_api_key": "test-key"})
        queries = [call.args[0] for call in mock_tmdb.call_args_list]
        self.assertEqual(queries[0], "Il Signore degli Anelli - Gli Anelli del Potere")
        self.assertTrue(any("Rings of Power" in q for q in queries))
        self.assertEqual(result["has_metadata"], 1)
        self.assertEqual(result["series_title"], "The Lord of the Rings: The Rings of Power")
        self.assertEqual(result["season_num"], 3)
        self.assertEqual(result["episode_num"], 1)


    @patch.object(meta, "download_poster_image_result", return_value={"local_poster_path": None, "poster_status": "missing", "poster_error": "No URL"})
    @patch.object(meta, "_fetch_tmdb_metadata")
    def test_enrichment_uses_cleaned_filename_when_legacy_series_title_is_noisy(self, mock_tmdb, _poster):
        mock_tmdb.return_value = None
        item = {
            "unique_key": "spiderman-2026", "file_name": "Spider-Man 9 Zbrusu nový den (2026) UHDRDV cz sk en.mkv",
            "parent_name": "Spider-Man 9 Zbrusu nový den (2026) UHDRDV cz sk en",
            "title": "Spider-Man 9 Zbrusu nový den UHDRDV cz sk en",
            "series_title": "Spider-Man 9 Zbrusu nový den UHDRDV cz sk en",
            "content_category": "general_movie", "year": 2026, "runtime": 145,
        }
        result = meta.enrich_media_item(item, {"tmdb_api_key": "test-key"})
        mock_tmdb.assert_called_once()
        self.assertEqual(mock_tmdb.call_args.args[0], "Spider-Man 9 Zbrusu nový den")
        self.assertEqual(result["has_metadata"], 0)
        self.assertEqual(result["_lookup"]["status"], "not_found")

    @patch.object(meta.HTTP_SESSION, "get")
    def test_tmdb_uses_canonical_alias_and_year_to_find_poster(self, mock_get):
        def fake_get(url, params=None, **kwargs):
            params = params or {}
            if url.endswith("/search/movie"):
                if params.get("query") == "Spider-Man: Brand New Day":
                    return FakeResponse({"results": [{
                        "id": 999, "title": "Spider-Man: Brand New Day",
                        "original_title": "Spider-Man: Brand New Day",
                        "release_date": "2026-07-31", "poster_path": "/brand-new-day.jpg",
                        "genre_ids": [28], "original_language": "en"
                    }]})
                return FakeResponse({"results": []})
            if url.endswith("/search/tv"):
                return FakeResponse({"results": []})
            if url.endswith("/movie/999"):
                return FakeResponse({
                    "id": 999, "title": "Spider-Man: Brand New Day",
                    "original_title": "Spider-Man: Brand New Day",
                    "release_date": "2026-07-31", "poster_path": "/brand-new-day.jpg",
                    "runtime": 145, "genres": [{"id": 28, "name": "Action"}],
                    "production_companies": [], "credits": {"cast": [], "crew": []},
                    "overview": "Peter Parker begins a brand new day."
                })
            raise AssertionError(f"Unexpected URL: {url}")

        mock_get.side_effect = fake_get
        diagnostics = []
        result = meta._fetch_tmdb_metadata(
            "Spider-Man 9 Zbrusu nový den UHDRDV cz sk en", "test-key", year=2026,
            diagnostics=diagnostics
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["tmdb_id"], 999)
        self.assertEqual(result["poster_url"], "https://image.tmdb.org/t/p/w500/brand-new-day.jpg")
        self.assertEqual(result["_lookup"]["query"], "Spider-Man: Brand New Day")


class JavPosterFallbackIntegrationTests(unittest.TestCase):
    @patch.object(meta, "download_poster_image_result", side_effect=[
        {"local_poster_path": None, "poster_status": "unreachable", "poster_error": "JavDatabase image host timeout"},
        {"local_poster_path": "tpdb-poster.jpg", "poster_status": "found", "poster_error": None},
    ])
    @patch.object(meta, "_fetch_tpdb_jav_metadata")
    @patch.object(meta, "_fetch_jav_metadata")
    def test_dead_javdatabase_poster_triggers_tpdb_fallback(self, mock_jav, mock_tpdb, _download):
        mock_jav.return_value = {
            "title": "Original provider title", "series_title": "Original provider title",
            "content_category": "jav", "is_adult": 1, "has_metadata": 1,
            "metadata_source": "javdatabase", "poster_url": "https://jdb.example/dead.jpg",
        }
        mock_tpdb.return_value = {
            "title": "TPDB title", "series_title": "TPDB title", "content_category": "jav",
            "is_adult": 1, "has_metadata": 1, "metadata_source": "theporndb_jav",
            "poster_url": "https://tpdb.example/cover.jpg",
        }
        item = {
            "unique_key": "jav-081326-001", "file_name": "Caribbeancom 081326-001.mp4",
            "parent_name": "Caribbeancom 13 08 2026 Beautiful Jeans Vol 33",
            "code_id": "081326-001", "content_category": "jav", "runtime": 59,
        }
        result = meta.enrich_media_item(item, {"tpdb_api_key": "test-key"})
        mock_tpdb.assert_called_once()
        self.assertEqual(result["metadata_source"], "theporndb_jav")
        self.assertEqual(result["poster_status"], "found")


class ThePornDbJavProviderTests(unittest.TestCase):
    @patch.object(meta.HTTP_SESSION, "get")
    def test_jav_endpoint_can_return_poster_for_date_code(self, mock_get):
        result_payload = {"data": [{
            "_id": "tpdb-jav-1", "id": 123, "title": "Beautiful Jeans Vol. 33",
            "external_id": "081326-001", "date": "2026-08-13", "duration": 3540,
            "poster": "https://cdn.example/081326-001.jpg", "image": None,
            "site": {"name": "Caribbeancom"}, "performers": [{"name": "Performer A"}],
            "tags": [{"name": "JAV"}], "rating": 0
        }]}
        mock_get.side_effect = [FakeResponse({"data": []}), FakeResponse(result_payload)]
        diagnostics = []
        result = meta._fetch_tpdb_jav_metadata(
            "081326-001", "tpdb-test-key",
            item={
                "file_name": "15.08.2026_Yurievij_Caribbeancom 081326-001 Beautiful Jeans Vol. 33.mp4",
                "parent_name": "Caribbeancom 13 08 2026 Beautiful Jeans Vol. 33 - Uncensored 081326-001"
            }, diagnostics=diagnostics
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["metadata_source"], "theporndb_jav")
        self.assertEqual(result["poster_url"], "https://cdn.example/081326-001.jpg")
        self.assertEqual(result["runtime"], 59)
        self.assertEqual(diagnostics[-1]["status"], "matched")
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(
            mock_get.call_args_list[1].kwargs["params"]["parse"],
            "caribbeancom.2026-08-13.Beautiful Jeans Vol 33",
        )


if __name__ == "__main__":
    unittest.main()
