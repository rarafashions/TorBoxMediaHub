"""Classification verdict persistence: batch reclassification and D3 registration."""
import unittest
from unittest.mock import MagicMock, patch

import app as app_module


class CategoryChangePolicyTests(unittest.TestCase):
    """Every verdict is recorded; only provable category moves are applied."""

    def _allowed(self, old, status="classified", new="tv_show"):
        return app_module._reclassification_category_change_allowed(
            old, {"classification_status": status, "detected_category": new}
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