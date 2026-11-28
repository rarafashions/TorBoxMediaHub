import os
import sqlite3
import tempfile
import unittest

import database_manager as db
import classification_engine as classifier


class ClassificationDatabaseFixtureMixin:
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_dir = db.DB_DIR
        self.old_db_path = db.DB_PATH
        db.DB_DIR = self.temp_dir.name
        db.DB_PATH = os.path.join(self.temp_dir.name, "torbox_media_hub.db")
        os.makedirs(db.DB_DIR, exist_ok=True)
        self._create_v11_fixture()

    def tearDown(self):
        db.DB_DIR = self.old_db_dir
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def _create_v11_fixture(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_key TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content_category TEXT NOT NULL DEFAULT 'uncategorized',
                is_adult INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                media_id INTEGER NOT NULL,
                sort_order INTEGER DEFAULT 0,
                added_at TEXT NOT NULL,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
                FOREIGN KEY (media_id) REFERENCES media_items(id) ON DELETE CASCADE
            );
            INSERT INTO media_items (unique_key, file_name, title, content_category, created_at, updated_at)
            VALUES
              ('old-1', 'IPX-123.mkv', 'Existing JAV', 'jav', '2026-01-01', '2026-01-01'),
              ('old-2', 'Show.S01E01.mkv', 'Existing Show', 'tv_show', '2026-01-02', '2026-01-02');
            """
        )
        conn.commit()
        conn.close()



class ClassificationDatabaseMigrationTests(ClassificationDatabaseFixtureMixin, unittest.TestCase):
    def test_connection_context_manager_closes_sqlite_handle(self):
        with db.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_migration_is_additive_and_preserves_existing_categories(self):
        conn = db.get_connection()
        before = [tuple(r) for r in conn.execute(
            "SELECT id, unique_key, content_category FROM media_items ORDER BY id"
        )]
        db.migrate_classification_schema(conn)
        after = [tuple(r) for r in conn.execute(
            "SELECT id, unique_key, content_category FROM media_items ORDER BY id"
        )]
        self.assertEqual(before, after)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(media_items)")}
        self.assertTrue({
            "normalized_title", "classification_status", "classification_method",
            "category_confidence", "classification_signals_json",
            "classification_locked", "classification_updated_at",
        }.issubset(cols))
        rows = conn.execute(
            "SELECT classification_status, classification_locked FROM media_items ORDER BY id"
        ).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("pending", 0), ("pending", 0)])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_classification_history").fetchone()[0], 0)
        conn.close()

    def test_migration_is_idempotent(self):
        with db.get_connection() as conn:
            db.migrate_classification_schema(conn)
            db.migrate_classification_schema(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = ?",
                (db.CLASSIFICATION_MIGRATION_ID,),
            ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0], 2)

    def test_missing_media_table_rolls_back_migration(self):
        conn = sqlite3.connect(":memory:")
        with self.assertRaises(RuntimeError):
            db.migrate_classification_schema(conn)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("schema_migrations", tables)
        conn.close()



class ClassificationDatabaseOperationsTests(ClassificationDatabaseFixtureMixin, unittest.TestCase):
    def test_classifier_result_and_needs_review_semantics(self):
        db.migrate_classification_schema()
        item = db.save_classification_result(
            1,
            {
                "classification_status": "classified",
                "detected_category": "english_adult",
                "normalized_title": "Existing JAV normalized",
                "classification_method": "explicit_adult_studio",
                "category_confidence": 99,
                "signals": ["studio_tag"],
            },
        )
        self.assertEqual(item["content_category"], "english_adult")
        self.assertEqual(item["is_adult"], 1)
        self.assertEqual(item["classification_status"], "classified")

        review = db.save_classification_result(
            1,
            {
                "classification_status": "needs_review",
                "detected_category": None,
                "normalized_title": "Needs Human Review",
                "classification_method": "ambiguous",
                "category_confidence": 40,
                "signals": ["ambiguous_code"],
            },
        )
        self.assertEqual(review["content_category"], "english_adult")
        self.assertEqual(review["classification_status"], "needs_review")
        self.assertEqual(len(db.get_classification_history(1)), 2)

    def test_manual_override_locks_against_classifier(self):
        db.migrate_classification_schema()
        item = db.apply_manual_classification(
            1, category="jav", normalized_title="Manual Corrected Title", note="User confirmed"
        )
        self.assertEqual(item["classification_status"], "manually_classified")
        self.assertEqual(item["is_adult"], 1)
        self.assertEqual(item["classification_locked"], 1)
        with self.assertRaises(PermissionError):
            db.save_classification_result(
                1,
                {
                    "classification_status": "classified",
                    "detected_category": "english_adult",
                    "normalized_title": "Auto Guess",
                    "classification_method": "test",
                    "category_confidence": 90,
                    "signals": [],
                },
            )
        unlocked = db.set_classification_lock(1, False)
        self.assertEqual(unlocked["classification_locked"], 0)
        self.assertGreaterEqual(len(db.get_classification_history(1)), 2)

    def test_invalid_result_does_not_change_data(self):
        db.migrate_classification_schema()
        with self.assertRaises(ValueError):
            db.save_classification_result(
                1,
                {
                    "classification_status": "classified",
                    "detected_category": "not_a_category",
                },
            )
        item = db.get_media_item(1)
        self.assertEqual(item["content_category"], "jav")
        self.assertEqual(item["classification_status"], "pending")

    def _insert_reclass_row(self, unique_key: str, category: str = "english_adult") -> int:
        with db.get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, content_category, "
                "classification_status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (unique_key, "Tante di Jakarta 720p.mp4", "Tante di Jakarta",
                 category, "pending", "2026-11-01", "2026-11-01"),
            )
            return int(cur.lastrowid)

    def test_reclassify_candidates_include_stale_categorized_rows(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("stale-1")
        candidates = db.get_reclassify_candidates()
        keys = [c["unique_key"] for c in candidates]
        self.assertIn("stale-1", keys)
        # Every stale row is returned so the worker can recover an Indo item that
        # was stored in the wrong bucket. Applying the verdict stays conservative.
        self.assertIn("old-2", keys)

        item = next(c for c in candidates if c["unique_key"] == "stale-1")
        result = classifier.classify_media(
            item["file_name"],
            item.get("parent_name") or "",
            normalized_title=item.get("title"),
        )
        saved = db.save_classification_result(media_id, result, changed_by="classifier")
        self.assertEqual(saved["content_category"], "indo")
        self.assertEqual(saved["classifier_version"], classifier.CLASSIFIER_VERSION)
        self.assertNotIn("stale-1", [c["unique_key"] for c in db.get_reclassify_candidates()])

    def test_mark_classifier_version_keeps_pending_row_eligible(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("mark-1", category="tv_show")
        self.assertTrue(db.mark_classifier_version(media_id))
        item = db.get_media_item(media_id)
        self.assertEqual(item["content_category"], "tv_show")
        self.assertEqual(item["classifier_version"], classifier.CLASSIFIER_VERSION)
        # A version stamp cannot retire a row whose verdict was never recorded.
        self.assertIn("mark-1", [c["unique_key"] for c in db.get_reclassify_candidates()])

    def test_stamped_pending_row_stays_a_reclassify_candidate(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("stamped-1", category="tv_show")
        db.mark_classifier_version(media_id)
        keys = [c["unique_key"] for c in db.get_reclassify_candidates()]
        self.assertIn("stamped-1", keys)

    def test_save_classification_records_status_without_touching_category_or_title(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("keep-1", category="tv_show")
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET normalized_title = ? WHERE id = ?",
                ("Canonical Provider Title", media_id),
            )
        saved = db.save_classification_result(
            media_id,
            {
                "classification_status": "classified",
                "detected_category": "general_movie",
                "normalized_title": "Filename Guess",
                "classification_method": "title_plus_year",
                "category_confidence": 82,
                "signals": [],
            },
            apply_category=False,
            apply_title=False,
        )
        self.assertEqual(saved["content_category"], "tv_show")
        self.assertEqual(saved["normalized_title"], "Canonical Provider Title")
        self.assertEqual(saved["classification_status"], "classified")
        self.assertEqual(saved["classification_method"], "title_plus_year")

    def test_reclassify_skips_manual_classifications(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("manual-1")
        db.apply_manual_classification(
            media_id, category="english_adult", normalized_title="Manual Title", note="user confirmed"
        )
        self.assertNotIn("manual-1", [c["unique_key"] for c in db.get_reclassify_candidates()])

    def test_manual_status_requires_force_to_overwrite(self):
        db.migrate_classification_schema()
        media_id = self._insert_reclass_row("manual-2")
        db.apply_manual_classification(
            media_id, category="english_adult", normalized_title="Manual Title", lock=False
        )
        with self.assertRaises(PermissionError):
            db.save_classification_result(
                media_id,
                {
                    "classification_status": "classified",
                    "detected_category": "indo",
                    "normalized_title": "Auto Guess",
                    "classification_method": "test",
                    "category_confidence": 94,
                    "signals": [],
                },
            )
        forced = db.save_classification_result(
            media_id,
            {
                "classification_status": "classified",
                "detected_category": "indo",
                "normalized_title": "Auto Guess",
                "classification_method": "test",
                "category_confidence": 94,
                "signals": [],
            },
            force=True,
        )
        self.assertEqual(forced["content_category"], "indo")


if __name__ == "__main__":
    unittest.main()
