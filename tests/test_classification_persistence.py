"""Classification verdict persistence: batch reclassification and D3 registration."""
import unittest
from unittest.mock import MagicMock, patch

import app as app_module


class CategoryChangePolicyTests(unittest.TestCase):
    """Every verdict is recorded; only provable category moves are applied."""

    def _allowed(self, old, status="classified", new="tv_show", item=None):
        return app_module._reclassification_category_change_allowed(
            old, {"classification_status": status, "detected_category": new}, item
        )

    def test_uncategorized_rows_accept_any_classified_verdict(self):
        self.assertTrue(self._allowed("uncategorized", new="general_movie"))
        self.assertTrue(self._allowed("", new="jav"))

    def test_needs_review_never_moves_a_category(self):
        self.assertFalse(self._allowed("tv_show", status="needs_review", new="tv_show"))
        self.assertFalse(self._allowed("general_movie", status="needs_review", new="indo"))

    def test_episodic_family_can_be_refined_but_not_demoted(self):
        self.assertTrue(self._allowed("tv_show", new="anime"))
        self.assertTrue(self._allowed("anime", new="donghua"))
        self.assertFalse(self._allowed("tv_show", new="general_movie"))

    def test_general_movie_accepts_episodic_or_adult_verdicts(self):
        self.assertTrue(self._allowed("general_movie", new="tv_show"))
        self.assertTrue(self._allowed("general_movie", new="english_adult"))
        self.assertTrue(self._allowed("general_movie", new="indo"))
        self.assertFalse(self._allowed("general_movie", new="general_movie"))

    def test_manifest_categories_are_never_rewritten_by_the_batch(self):
        self.assertFalse(self._allowed("indo_movie", new="general_movie"))
        self.assertFalse(self._allowed("donghua", new="tv_show"))

    def test_adult_rows_accept_adult_refinements(self):
        self.assertTrue(self._allowed("english_adult", new="indo"))
        self.assertTrue(self._allowed("jav", new="jav"))

    def test_provider_backed_rows_keep_their_category(self):
        tmdb_row = {"metadata_source": "tmdb", "code_id": None}
        self.assertFalse(self._allowed("tv_show", new="anime", item=tmdb_row))
        self.assertFalse(self._allowed("general_movie", new="tv_show", item=tmdb_row))
        self.assertFalse(
            self._allowed("general_movie", new="tv_show", item={"metadata_source": "anilist"})
        )

    def test_adult_evidence_promotes_even_a_provider_backed_row(self):
        tmdb_row = {"metadata_source": "tmdb"}
        self.assertTrue(self._allowed("general_movie", new="indo", item=tmdb_row))
        self.assertTrue(self._allowed("general_movie", new="jav", item=tmdb_row))

    def test_validated_code_pins_a_jav_row(self):
        self.assertFalse(
            self._allowed("jav", new="english_adult", item={"metadata_source": "javdatabase", "code_id": "SNOS-338"})
        )

    def test_english_adult_from_manifest_may_be_promoted_to_indo(self):
        roxxy_row = {"metadata_source": "roxxy"}
        self.assertTrue(self._allowed("english_adult", new="indo", item=roxxy_row))


class EvidenceBackedCategoryTests(unittest.TestCase):
    """A provider category survives a thinner second read."""

    def _preserve(self, old, item, status="needs_review", verdict=None):
        return app_module._preserve_evidence_backed_category(
            old, item, {"classification_status": status, "detected_category": verdict}
        )

    def test_provider_row_gets_a_kept_verdict(self):
        kept = self._preserve("tv_show", {"metadata_source": "tmdb"})
        self.assertEqual(kept["classification_status"], "classified")
        self.assertEqual(kept["detected_category"], "tv_show")
        self.assertEqual(kept["classification_method"], "kept_stored_category")

    def test_agreeing_verdict_is_left_alone(self):
        result = {"classification_status": "classified", "detected_category": "tv_show"}
        self.assertIs(
            app_module._preserve_evidence_backed_category("tv_show", {"metadata_source": "tmdb"}, result),
            result,
        )

    def test_weak_adult_manifest_and_uncategorized_rows_are_untouched(self):
        weak = {"metadata_source": "filename_parser"}
        result = {"classification_status": "needs_review", "detected_category": None}
        for old, item in (
            ("tv_show", weak),
            ("english_adult", {"metadata_source": "theporndb"}),
            ("jav", {"metadata_source": "javdatabase"}),
            ("indo_movie", {"metadata_source": "roxxy"}),
            ("uncategorized", {"metadata_source": "tmdb"}),
        ):
            self.assertIs(
                app_module._preserve_evidence_backed_category(old, item, result), result,
                msg=old,
            )


class ReclassifyWorkerPersistenceTests(unittest.TestCase):
    def _run(self, item, verdict):
        saved = {}

        def fake_save(media_id, result, **kwargs):
            saved["media_id"] = media_id
            saved["result"] = result
            saved["kwargs"] = kwargs
            return {"id": media_id, "classification_status": result.get("classification_status")}

        task = MagicMock()
        with patch.object(app_module.db, "get_reclassify_candidates", return_value=[item]), \
                patch.object(app_module.class_eng, "classify_media", return_value=verdict), \
                patch.object(app_module.db, "save_classification_result", side_effect=fake_save), \
                patch.object(app_module, "TASK_MGR", task):
            app_module._classify_pending_worker()
        return saved, task

    def test_status_is_recorded_without_rewriting_provider_metadata(self):
        item = {
            "id": 501, "file_name": "Show.S01E01.1080p.mkv", "parent_name": "Show S01",
            "title": "Canonical Show", "normalized_title": "Canonical Show",
            "content_category": "tv_show", "has_metadata": 1, "title_is_manual": 0,
            "studio": "", "genres": [], "plot": "", "year": 2024,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "general_movie",
            "normalized_title": "Show", "classification_method": "title_plus_year",
            "category_confidence": 82, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["kwargs"]["apply_title"], False)
        self.assertEqual(saved["result"]["classification_status"], "classified")
        task.finish.assert_called_once()
        self.assertIn("1 classified", task.finish.call_args.args[0])

    def test_adult_verdict_recovers_a_general_movie_row(self):
        item = {
            "id": 502, "file_name": "Tante di Jakarta 720p.mp4", "parent_name": "",
            "title": "Tante di Jakarta", "normalized_title": None,
            "content_category": "general_movie", "has_metadata": 0, "title_is_manual": 0,
            "studio": "", "genres": [], "plot": "", "year": None,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "indo",
            "normalized_title": "Tante di Jakarta", "classification_method": "indo_keyword_classifier",
            "category_confidence": 88, "signals": ["indo:title"],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], True)
        self.assertEqual(saved["kwargs"]["apply_title"], True)
        self.assertEqual(saved["result"]["detected_category"], "indo")

    def test_stored_jav_code_wins_over_a_weaker_adult_verdict(self):
        item = {
            "id": 504, "file_name": "SNOS-338.mkv", "parent_name": "",
            "title": "JAV Title", "normalized_title": None,
            "content_category": "jav", "has_metadata": 1, "title_is_manual": 0,
            "metadata_source": "javdatabase", "code_id": "SNOS-338",
            "studio": "", "genres": [], "plot": "", "year": 2024,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "english_adult",
            "normalized_title": "JAV Title", "classification_method": "explicit_adult_keyword",
            "category_confidence": 90, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["result"]["detected_category"], "jav")
        self.assertEqual(saved["result"]["classification_method"], "stored_jav_code")
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["result"]["classification_status"], "classified")

    def test_jav_without_code_keeps_category_and_flags_review(self):
        item = {
            "id": 505, "file_name": "unknown-release.mkv", "parent_name": "",
            "title": "Unknown JAV", "normalized_title": None,
            "content_category": "jav", "has_metadata": 0, "title_is_manual": 0,
            "metadata_source": "jav_code_parser", "code_id": None,
            "studio": "", "genres": [], "plot": "", "year": None,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "general_movie",
            "normalized_title": "Unknown JAV", "classification_method": "title_plus_year",
            "category_confidence": 82, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["result"]["classification_status"], "needs_review")
        self.assertIsNone(saved["result"]["detected_category"])
        self.assertIn("1 needs review", task.finish.call_args.args[0])

    def test_filename_parsed_jav_code_still_needs_verification(self):
        item = {
            "id": 509, "file_name": "IMJO-015.mp4", "parent_name": "",
            "title": "IMJO-015", "normalized_title": "IMJO-015",
            "content_category": "jav", "has_metadata": 0, "title_is_manual": 0,
            "metadata_source": "jav_code_parser", "code_id": "IMJO-015",
            "studio": "", "genres": [], "plot": "", "year": None,
        }
        verdict = {
            "classification_status": "needs_review", "detected_category": None,
            "normalized_title": "IMJO-015", "classification_method": "unverified_code",
            "category_confidence": 0, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["result"]["classification_status"], "needs_review")
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertIn("1 needs review", task.finish.call_args.args[0])

    def test_provider_confirmed_code_pins_jav_even_without_a_fresh_verdict(self):
        item = {
            "id": 510, "file_name": "START-672.mp4", "parent_name": "",
            "title": "START-672", "normalized_title": "START-672",
            "content_category": "jav", "has_metadata": 1, "title_is_manual": 0,
            "metadata_source": "javdatabase", "code_id": "START-672",
            "studio": "", "genres": [], "plot": "", "year": 2024,
        }
        verdict = {
            "classification_status": "needs_review", "detected_category": None,
            "normalized_title": "START-672", "classification_method": "no_engine_matched",
            "category_confidence": 0, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["result"]["classification_status"], "classified")
        self.assertEqual(saved["result"]["detected_category"], "jav")
        self.assertEqual(saved["result"]["classification_method"], "stored_jav_code")
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertIn("1 classified", task.finish.call_args.args[0])

    def test_provider_row_records_kept_category_instead_of_false_review(self):
        # A TMDb episode keeps its show category even when the stripped file name
        # no longer proves an episode; that must not enter the review queue.
        item = {
            "id": 506, "file_name": "Il Signore degli Anelli 3x01 Marnukh ITA.mkv",
            "parent_name": "", "title": "The Rings of Power", "normalized_title": "The Rings of Power",
            "content_category": "tv_show", "has_metadata": 1, "title_is_manual": 0,
            "metadata_source": "tmdb", "code_id": None,
            "studio": "", "genres": [], "plot": "", "year": 2024,
        }
        verdict = {
            "classification_status": "needs_review", "detected_category": None,
            "normalized_title": "Il Signore degli Anelli", "classification_method": "filename_parser",
            "category_confidence": 40, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["kwargs"]["apply_title"], False)
        self.assertEqual(saved["result"]["classification_status"], "classified")
        self.assertEqual(saved["result"]["detected_category"], "tv_show")
        self.assertEqual(saved["result"]["classification_method"], "kept_stored_category")
        self.assertIn("stored_category:tv_show", saved["result"]["signals"])
        self.assertIn("1 classified", task.finish.call_args.args[0])

    def test_provider_row_with_different_verdict_keeps_its_category(self):
        item = {
            "id": 507, "file_name": "Show.S01E01.mkv", "parent_name": "",
            "title": "Show", "normalized_title": "Show",
            "content_category": "tv_show", "has_metadata": 1, "title_is_manual": 0,
            "metadata_source": "anilist", "code_id": None,
            "studio": "", "genres": [], "plot": "", "year": 2023,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "general_movie",
            "normalized_title": "Show", "classification_method": "title_plus_year",
            "category_confidence": 82, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["result"]["detected_category"], "tv_show")
        self.assertIn("classifier_verdict:general_movie", saved["result"]["signals"])

    def test_filename_only_row_still_needs_review_when_unprovable(self):
        item = {
            "id": 508, "file_name": "unknown-scene.mp4", "parent_name": "",
            "title": "Unknown Scene", "normalized_title": None,
            "content_category": "general_movie", "has_metadata": 0, "title_is_manual": 0,
            "metadata_source": "filename_parser", "code_id": None,
            "studio": "", "genres": [], "plot": "", "year": None,
        }
        verdict = {
            "classification_status": "needs_review", "detected_category": None,
            "normalized_title": "Unknown Scene", "classification_method": "filename_parser",
            "category_confidence": 30, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["result"]["classification_status"], "needs_review")
        self.assertIn("1 needs review", task.finish.call_args.args[0])

    def test_manifest_category_is_kept_and_flagged_for_review(self):
        item = {
            "id": 503, "file_name": "Battle.Through.The.Heavens.E219.mkv", "parent_name": "",
            "title": "BTTH", "normalized_title": None,
            "content_category": "donghua", "has_metadata": 1, "title_is_manual": 0,
            "studio": "", "genres": [], "plot": "", "year": None,
        }
        verdict = {
            "classification_status": "classified", "detected_category": "tv_show",
            "normalized_title": "Battle Through The Heavens",
            "classification_method": "episodic_signal", "category_confidence": 90, "signals": [],
        }
        saved, task = self._run(item, verdict)
        self.assertEqual(saved["kwargs"]["apply_category"], False)
        self.assertEqual(saved["result"]["classification_status"], "needs_review")
        self.assertIsNone(saved["result"]["detected_category"])
        self.assertIn("1 needs review", task.finish.call_args.args[0])


if __name__ == "__main__":
    unittest.main()