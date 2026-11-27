import unittest

import database_manager as db
import classification_engine as classifier
from test_classification_database import ClassificationDatabaseFixtureMixin


class ClassificationIntegrationDatabaseTests(ClassificationDatabaseFixtureMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        db.migrate_classification_schema()
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, content_category, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("new-uncat", "IPX-999.mkv", "IPX-999.mkv", "uncategorized", "2026-11-23", "2026-11-23"),
            )
            conn.commit()

    def test_batch_candidates_exclude_legacy_categorized_media(self):
        candidates = db.get_pending_classification_candidates()
        self.assertEqual([item["unique_key"] for item in candidates], ["new-uncat"])

    def test_classifier_result_is_saved_without_changing_original_filename(self):
        candidate = db.get_pending_classification_candidates()[0]
        result = classifier.classify_media(candidate["file_name"], candidate.get("parent_name") or "")
        saved = db.save_classification_result(candidate["id"], result, note="integration test")
        self.assertEqual(saved["file_name"], "IPX-999.mkv")
        self.assertEqual(saved["content_category"], "jav")
        self.assertEqual(saved["classification_status"], "classified")
        self.assertEqual(saved["normalized_title"], "IPX-999")

    def test_needs_review_does_not_replace_existing_category(self):
        result = classifier.classify_media("ABC-123.mkv")
        saved = db.save_classification_result(1, result)
        self.assertEqual(saved["content_category"], "jav")
        self.assertEqual(saved["classification_status"], "needs_review")

    def test_classification_status_filter(self):
        result = classifier.classify_media("ABC-123.mkv")
        db.save_classification_result(1, result)
        review_items = db.query_media_items(classification_status="needs_review")
        self.assertEqual([item["id"] for item in review_items], [1])



if __name__ == "__main__":
    unittest.main()
