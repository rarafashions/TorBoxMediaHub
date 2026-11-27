import os
import tempfile
import unittest

import database_manager as db


class ManualTitleEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._old_paths = {
            name: getattr(db, name)
            for name in ("DB_DIR", "DB_PATH", "BACKUP_DIR", "CACHE_DIR", "PLAYLIST_DIR")
        }
        db.DB_DIR = self.temp_dir.name
        db.DB_PATH = os.path.join(self.temp_dir.name, "torbox_media_hub.db")
        db.BACKUP_DIR = os.path.join(self.temp_dir.name, "backups")
        db.CACHE_DIR = os.path.join(self.temp_dir.name, "cache")
        db.PLAYLIST_DIR = os.path.join(self.temp_dir.name, "playlists")
        db.init_db()
        self.media_id = db.upsert_media_item({
            "unique_key": "jav-081326-001",
            "source_type": "cloud",
            "file_name": "081326-001.mp4",
            "title": "081326-001",
            "series_title": "081326-001",
            "content_category": "jav",
            "has_metadata": 1,
            "metadata_source": "jav_database",
        })
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET normalized_title = ?, title_is_manual = 0 WHERE id = ?",
                ("081326-001", self.media_id),
            )

    def tearDown(self):
        for name, value in self._old_paths.items():
            setattr(db, name, value)
        self.temp_dir.cleanup()

    def test_save_manual_title_updates_normalized_search_title(self):
        updated = db.update_media_manual(self.media_id, {
            "title": "Corrected Human Title",
            "series_title": "Corrected Human Title",
            "content_category": "jav",
            "code_id": "081326-001",
            "year": "2026",
            "runtime": "90",
            "studio": "Test Studio",
            "plot": "Manual note",
        })
        self.assertEqual(updated["title"], "Corrected Human Title")
        self.assertEqual(updated["series_title"], "Corrected Human Title")
        self.assertEqual(updated["normalized_title"], "Corrected Human Title")
        self.assertEqual(updated["title_is_manual"], 1)
        self.assertEqual(updated["metadata_source"], "manual")

    def test_force_enrichment_preserves_manual_title_but_refreshes_metadata(self):
        db.update_media_manual(self.media_id, {
            "title": "Corrected Human Title",
            "series_title": "Corrected Human Title",
            "content_category": "jav",
            "code_id": "081326-001",
            "year": "2026",
            "runtime": "90",
            "studio": "Test Studio",
            "plot": "Manual note",
        })
        db.upsert_media_item({
            "unique_key": "jav-081326-001",
            "file_name": "081326-001.mp4",
            "title": "Provider Returned Title",
            "series_title": "Provider Series Title",
            "content_category": "jav",
            "has_metadata": 1,
            "metadata_source": "jav_provider",
            "plot": "Fresh provider plot",
            "runtime": 95,
            "_force_meta": True,
        })
        item = db.get_media_item(self.media_id)
        self.assertEqual(item["title"], "Corrected Human Title")
        self.assertEqual(item["series_title"], "Corrected Human Title")
        self.assertEqual(item["normalized_title"], "Corrected Human Title")
        self.assertEqual(item["title_is_manual"], 1)
        self.assertEqual(item["plot"], "Fresh provider plot")
        self.assertEqual(item["runtime"], 95)
        self.assertEqual(item["metadata_source"], "jav_provider")

        # A second refresh must not lose the manual title after provider metadata_source changes.
        db.upsert_media_item({
            "unique_key": "jav-081326-001",
            "file_name": "081326-001.mp4",
            "title": "Another Provider Title",
            "series_title": "Another Provider Series",
            "content_category": "jav",
            "has_metadata": 1,
            "metadata_source": "another_provider",
            "plot": "Second provider plot",
            "_force_meta": True,
        })
        item = db.get_media_item(self.media_id)
        self.assertEqual(item["title"], "Corrected Human Title")
        self.assertEqual(item["series_title"], "Corrected Human Title")
        self.assertEqual(item["plot"], "Second provider plot")

    def test_title_override_migration_is_idempotent(self):
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET normalized_title = ? WHERE id = ?",
                ("First migrated title", self.media_id),
            )
        db.migrate_manual_title_override_schema()
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE media_items SET normalized_title = ? WHERE id = ?",
                ("Subsequent deliberate edit", self.media_id),
            )
        db.migrate_manual_title_override_schema()
        item = db.get_media_item(self.media_id)
        self.assertEqual(item["normalized_title"], "Subsequent deliberate edit")

    def test_migration_repairs_previously_saved_manual_title(self):
        with db.get_connection() as conn:
            conn.execute(
                """UPDATE media_items SET title = ?, series_title = ?, normalized_title = ?,
                   metadata_source = 'manual', title_is_manual = 0 WHERE id = ?""",
                ("Already Corrected", "Already Corrected", "081326-001", self.media_id),
            )
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                (db.TITLE_OVERRIDE_MIGRATION_ID,),
            )
        db.migrate_manual_title_override_schema()
        item = db.get_media_item(self.media_id)
        self.assertEqual(item["normalized_title"], "Already Corrected")
        self.assertEqual(item["title_is_manual"], 1)


if __name__ == "__main__":
    unittest.main()
