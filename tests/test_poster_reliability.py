import os
import importlib.util
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database_manager as db
import metadata_engine as meta


class PosterDatabaseFixture(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_dir, self.old_db_path = db.DB_DIR, db.DB_PATH
        db.DB_DIR = self.temp_dir.name
        db.DB_PATH = os.path.join(self.temp_dir.name, "poster_test.db")
        conn = sqlite3.connect(db.DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
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
                added_at TEXT NOT NULL
            );
            CREATE TABLE media_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unique_key TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                title TEXT NOT NULL,
                content_category TEXT NOT NULL DEFAULT 'uncategorized',
                is_adult INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                poster_url TEXT,
                local_poster_path TEXT
            );
            INSERT INTO media_items (unique_key, file_name, title, created_at, updated_at)
            VALUES ('poster-key', 'Movie.2024.mkv', 'Movie', '2026-01-01', '2026-01-01');
            """
        )
        conn.commit()
        conn.close()
        db.migrate_classification_schema()
        db.migrate_poster_schema()
        self.valid_poster_path = os.path.join(self.temp_dir.name, "manual-valid.jpg")
        with open(self.valid_poster_path, "wb") as poster_file:
            poster_file.write(b"\xff\xd8\xff" + (b"x" * 3000))

    def tearDown(self):
        db.DB_DIR, db.DB_PATH = self.old_db_dir, self.old_db_path
        self.temp_dir.cleanup()

    def test_poster_migration_additive_and_idempotent(self):
        with db.get_connection() as conn:
            before = conn.execute("SELECT id, unique_key, content_category FROM media_items").fetchall()
            db.migrate_poster_schema(conn)
            db.migrate_poster_schema(conn)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(media_items)")}
            after = conn.execute("SELECT id, unique_key, content_category FROM media_items").fetchall()
        self.assertTrue({"poster_status", "poster_checked_at", "poster_error", "poster_source", "poster_is_manual"}.issubset(columns))
        self.assertEqual([tuple(x) for x in before], [tuple(x) for x in after])

    def test_manual_poster_is_protected_from_automatic_update(self):
        self.assertTrue(db.update_media_poster_state(
            1, poster_url="https://images.example/manual.jpg", local_poster_path=self.valid_poster_path,
            status="manual", source="manual", is_manual=True, allow_manual_overwrite=True,
        ))
        db.update_media_poster_state(
            1, poster_url="https://images.example/automatic.jpg", local_poster_path="automatic.jpg",
            status="found", source="tmdb",
        )
        item = db.get_media_item(1)
        self.assertEqual(item["poster_url"], "https://images.example/manual.jpg")
        self.assertEqual(item["local_poster_path"], self.valid_poster_path)
        self.assertEqual(item["poster_status"], "manual")
        self.assertEqual(item["poster_is_manual"], 1)

    def test_manual_poster_load_error_does_not_clear_selection(self):
        db.update_media_poster_state(
            1, poster_url="https://images.example/manual.jpg", local_poster_path="manual.jpg",
            status="manual", source="manual", is_manual=True, allow_manual_overwrite=True,
        )
        db.update_media_poster_state(1, status="load_error", error="browser error", source="browser")
        item = db.get_media_item(1)
        self.assertEqual(item["poster_url"], "https://images.example/manual.jpg")
        self.assertEqual(item["poster_status"], "load_error")
        self.assertEqual(item["poster_is_manual"], 1)

    def test_clear_manual_poster_releases_lock(self):
        db.update_media_poster_state(
            1, poster_url="https://images.example/manual.jpg", local_poster_path="manual.jpg",
            status="manual", source="manual", is_manual=True, allow_manual_overwrite=True,
        )
        self.assertTrue(db.clear_manual_poster_selection(1))
        item = db.get_media_item(1)
        self.assertIsNone(item["poster_url"])
        self.assertEqual(item["poster_is_manual"], 0)
        self.assertEqual(item["poster_status"], "not_checked")

    def test_automatic_lookup_can_clear_stale_url(self):
        with db.get_connection() as conn:
            conn.execute("UPDATE media_items SET poster_url = ? WHERE id = 1", ("https://old.example/poster.jpg",))
        db.update_media_poster_state(1, poster_url=None, status="missing", replace_url=True)
        with db.get_connection() as conn:
            row = conn.execute("SELECT poster_url, poster_status FROM media_items WHERE id = 1").fetchone()
        self.assertIsNone(row["poster_url"])
        self.assertEqual(row["poster_status"], "missing")

    def test_invalid_poster_status_is_rejected(self):
        with self.assertRaises(ValueError):
            db.update_media_poster_state(1, status="made_up")


class FakeResponse:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {"Content-Type": "image/jpeg"}

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        pass


class PosterDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_cache = meta.POSTER_CACHE_DIR
        meta.POSTER_CACHE_DIR = self.temp_dir.name
        self.image = b"\xff\xd8\xff" + (b"x" * 3000)

    def tearDown(self):
        meta.POSTER_CACHE_DIR = self.old_cache
        self.temp_dir.cleanup()

    @patch.object(meta, "_poster_url_is_public", return_value=(True, ""))
    @patch.object(meta.HTTP_SESSION, "get")
    def test_valid_jpeg_downloaded_atomically(self, mock_get, _public):
        mock_get.return_value = FakeResponse(content=self.image)
        result = meta.download_poster_image_result("https://cdn.example/poster", "media-1")
        self.assertEqual(result["poster_status"], "found")
        self.assertTrue(os.path.isfile(result["local_poster_path"]))
        with open(result["local_poster_path"], "rb") as f:
            self.assertTrue(f.read(3).startswith(b"\xff\xd8\xff"))
        self.assertFalse(any(name.startswith(".poster_") for name in os.listdir(self.temp_dir.name)))

    @patch.object(meta, "_poster_url_is_public", return_value=(True, ""))
    @patch.object(meta.HTTP_SESSION, "get")
    def test_html_response_is_rejected(self, mock_get, _public):
        mock_get.return_value = FakeResponse(content=b"<html>" + (b"x" * 3000), headers={"Content-Type": "text/html"})
        result = meta.download_poster_image_result("https://cdn.example/poster", "media-2")
        self.assertEqual(result["poster_status"], "invalid_image")
        self.assertIsNone(result["local_poster_path"])
        self.assertEqual(os.listdir(self.temp_dir.name), [])

    def test_private_host_is_rejected_before_request(self):
        with patch.object(meta.HTTP_SESSION, "get") as mock_get:
            result = meta.download_poster_image_result("http://127.0.0.1/poster.jpg", "media-3")
        self.assertEqual(result["poster_status"], "invalid_url")
        mock_get.assert_not_called()

    @patch.object(meta, "_poster_url_is_public", return_value=(True, ""))
    @patch.object(meta.HTTP_SESSION, "get")
    def test_oversized_response_is_rejected(self, mock_get, _public):
        mock_get.return_value = FakeResponse(content=b"\xff\xd8\xff" + (b"x" * (15 * 1024 * 1024)))
        result = meta.download_poster_image_result("https://cdn.example/huge", "media-huge")
        self.assertEqual(result["poster_status"], "invalid_image")
        self.assertIn("15 MB", result["poster_error"])
        self.assertEqual(os.listdir(self.temp_dir.name), [])

    def test_missing_url_has_explicit_status(self):
        result = meta.download_poster_image_result(None, "media-4")
        self.assertEqual(result["poster_status"], "missing")

    @patch.object(meta, "_poster_url_is_public", side_effect=[(True, ""), (False, "blocked redirect")])
    @patch.object(meta.HTTP_SESSION, "get")
    def test_redirect_to_private_target_is_blocked(self, mock_get, _public):
        mock_get.return_value = FakeResponse(status_code=302, headers={"Location": "http://127.0.0.1/secret"})
        result = meta.download_poster_image_result("https://cdn.example/redirect", "media-5")
        self.assertEqual(result["poster_status"], "invalid_url")
        mock_get.assert_called_once()

    @patch.object(meta, "_poster_url_is_public", return_value=(True, ""))
    @patch.object(meta.HTTP_SESSION, "get")
    def test_valid_cache_is_used_without_network(self, mock_get, _public):
        mock_get.return_value = FakeResponse(content=self.image)
        first = meta.download_poster_image_result("https://cdn.example/cached", "media-cache")
        mock_get.reset_mock()
        second = meta.download_poster_image_result("https://cdn.example/cached", "media-cache")
        self.assertEqual(first["local_poster_path"], second["local_poster_path"])
        mock_get.assert_not_called()

    @patch.object(meta, "_poster_url_is_public", return_value=(True, ""))
    @patch.object(meta.HTTP_SESSION, "get")
    def test_changed_url_uses_distinct_cache_file(self, mock_get, _public):
        mock_get.return_value = FakeResponse(content=self.image)
        first = meta.download_poster_image_result("https://cdn.example/poster-a", "same-media")
        second = meta.download_poster_image_result("https://cdn.example/poster-b", "same-media")
        self.assertNotEqual(first["local_poster_path"], second["local_poster_path"])
        self.assertEqual(len(os.listdir(self.temp_dir.name)), 2)


class MetadataEnrichmentPosterIntegrationTests(unittest.TestCase):
    @patch.object(meta, "probe_video_duration_minutes", return_value=0)
    @patch.object(meta, "download_poster_image_result", return_value={
        "local_poster_path": None, "poster_status": "missing", "poster_error": "No poster"
    })
    @patch.object(meta, "_fetch_anilist_metadata", return_value={
        "title": "My Confirmed Anime", "series_title": "My Confirmed Anime",
        "content_category": "anime", "has_metadata": 1, "metadata_source": "anilist",
        "poster_url": None,
    })
    def test_confirmed_category_and_normalized_title_drive_metadata_lookup(self, _anilist, _poster, _probe):
        item = {
            "unique_key": "anime-1", "file_name": "random_name_2024.mkv",
            "normalized_title": "My Confirmed Anime", "content_category": "anime",
            "poster_is_manual": 0,
        }
        result = meta.enrich_media_item(item, {})
        _anilist.assert_called_once_with("My Confirmed Anime", force_donghua=False)
        self.assertEqual(result["content_category"], "anime")
        self.assertEqual(result["series_title"], "My Confirmed Anime")
        self.assertEqual(result["poster_status"], "missing")

    @patch.object(meta, "probe_video_duration_minutes", return_value=0)
    @patch.object(meta, "download_poster_image_result", return_value={
        "local_poster_path": None, "poster_status": "missing", "poster_error": "No poster"
    })
    @patch.object(meta, "_fetch_jav_metadata", return_value={
        "title": "Provider Returned Title", "series_title": "Provider Returned Title",
        "content_category": "jav", "has_metadata": 1, "metadata_source": "jav_provider",
        "poster_url": None,
    })
    def test_manual_title_override_remains_canonical_after_provider_lookup(self, _jav, _poster, _probe):
        item = {
            "unique_key": "jav-manual-1", "file_name": "081326-001.mp4",
            "normalized_title": "Corrected Human Title", "title_is_manual": 1,
            "code_id": "081326-001", "content_category": "jav", "poster_is_manual": 0,
        }
        result = meta.enrich_media_item(item, {})
        _jav.assert_called_once_with("081326-001", "", item={"unique_key": "jav-manual-1", "file_name": "081326-001.mp4", "normalized_title": "Corrected Human Title", "title_is_manual": 1, "code_id": "081326-001", "content_category": "jav", "poster_is_manual": 0}, diagnostics=[])
        self.assertEqual(result["series_title"], "Corrected Human Title")

    @patch.object(meta, "probe_video_duration_minutes", return_value=0)
    @patch.object(meta, "download_poster_image_result")
    @patch.object(meta, "_fetch_tmdb_metadata", return_value={
        "title": "Confirmed Show", "series_title": "Confirmed Show",
        "content_category": "tv_show", "has_metadata": 1, "metadata_source": "tmdb",
        "poster_url": "https://cdn.example/automatic.jpg",
    })
    def test_manual_poster_is_not_redownloaded_or_replaced(self, _tmdb, poster_download, _probe):
        item = {
            "unique_key": "show-1", "file_name": "unknown_file.mkv",
            "normalized_title": "Confirmed Show", "content_category": "tv_show",
            "poster_is_manual": 1, "poster_url": "https://cdn.example/manual.jpg",
            "local_poster_path": "manual-poster.jpg", "poster_error": None,
        }
        result = meta.enrich_media_item(item, {"tmdb_api_key": "test"})
        _tmdb.assert_called_once_with("Confirmed Show", "test", year=None, prefer_tv=True, diagnostics=[])
        poster_download.assert_not_called()
        self.assertEqual(result["poster_url"], "https://cdn.example/manual.jpg")
        self.assertEqual(result["local_poster_path"], "manual-poster.jpg")
        self.assertEqual(result["poster_status"], "manual")



if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(importlib.util.find_spec("flask"), "Flask is not installed in this isolated test environment")
class PosterApiTests(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_paths = {
            "DB_DIR": db.DB_DIR, "DB_PATH": db.DB_PATH, "BACKUP_DIR": db.BACKUP_DIR,
            "CACHE_DIR": db.CACHE_DIR, "PLAYLIST_DIR": db.PLAYLIST_DIR,
            "STATIC_DIR": db.STATIC_DIR,
        }
        db.DB_DIR = os.path.join(self.temp_dir.name, "database")
        db.DB_PATH = os.path.join(db.DB_DIR, "test.db")
        db.BACKUP_DIR = os.path.join(self.temp_dir.name, "backups")
        db.CACHE_DIR = os.path.join(self.temp_dir.name, "cache")
        db.PLAYLIST_DIR = os.path.join(self.temp_dir.name, "playlists")
        db.STATIC_DIR = os.path.join(self.temp_dir.name, "static")
        db.init_db()
        self.poster_file = os.path.join(self.temp_dir.name, "api-poster.jpg")
        with open(self.poster_file, "wb") as poster_file:
            poster_file.write(b"\xff\xd8\xff" + (b"x" * 3000))
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("api-poster-key", "API.Test.mkv", "API Test", "2026-01-01", "2026-01-01"),
            )
        self.client = app_module.app.test_client()

    def tearDown(self):
        for name, value in self.old_paths.items():
            setattr(db, name, value)
        self.temp_dir.cleanup()

    def test_manual_poster_api_sets_manual_lock(self):
        with patch.object(meta, "download_poster_image_result", return_value={
            "local_poster_path": self.poster_file, "poster_status": "found", "poster_error": None
        }):
            response = self.client.post(
                "/api/media/1/poster/manual",
                json={"poster_url": "https://images.example/manual.jpg"},
            )
        self.assertEqual(response.status_code, 200)
        item = db.get_media_item(1)
        self.assertEqual(item["poster_status"], "manual")
        self.assertEqual(item["poster_is_manual"], 1)
        self.assertEqual(item["poster_url"], "https://images.example/manual.jpg")

    def test_retry_poster_api_updates_poster_without_metadata_upsert(self):
        with db.get_connection() as conn:
            conn.execute("UPDATE media_items SET poster_url = ? WHERE id = 1", ("https://images.example/current.jpg",))
        with patch.object(meta, "download_poster_image_result", return_value={
            "local_poster_path": self.poster_file, "poster_status": "found", "poster_error": None
        }):
            response = self.client.post("/api/media/1/poster/retry")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        item = db.get_media_item(1)
        self.assertEqual(item["poster_status"], "found")
        self.assertEqual(item["local_poster_path"], self.poster_file)

    def test_browser_load_error_is_recorded(self):
        response = self.client.post("/api/media/1/poster/load-error")
        self.assertEqual(response.status_code, 200)
        item = db.get_media_item(1)
        self.assertEqual(item["poster_status"], "load_error")



class PosterStartupMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_paths = {
            "DB_DIR": db.DB_DIR, "DB_PATH": db.DB_PATH, "BACKUP_DIR": db.BACKUP_DIR,
            "CACHE_DIR": db.CACHE_DIR, "PLAYLIST_DIR": db.PLAYLIST_DIR,
            "STATIC_DIR": db.STATIC_DIR,
        }
        db.DB_DIR = os.path.join(self.temp_dir.name, "database")
        db.DB_PATH = os.path.join(db.DB_DIR, "torbox_media_hub.db")
        db.BACKUP_DIR = os.path.join(self.temp_dir.name, "backups")
        db.CACHE_DIR = os.path.join(self.temp_dir.name, "cache")
        db.PLAYLIST_DIR = os.path.join(self.temp_dir.name, "playlists")
        db.STATIC_DIR = os.path.join(self.temp_dir.name, "static")
        db.init_db()

    def tearDown(self):
        for name, value in self.old_paths.items():
            setattr(db, name, value)
        self.temp_dir.cleanup()

    def test_startup_migration_creates_pre_migration_backup(self):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, content_category, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("startup-old", "Old.Movie.mkv", "Old Movie", "general_movie", "2026-01-01", "2026-01-01"),
            )
            conn.execute("DROP INDEX IF EXISTS idx_media_poster_status")
            for column in ("poster_status", "poster_checked_at", "poster_error", "poster_source", "poster_is_manual"):
                conn.execute(f"ALTER TABLE media_items DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_migrations WHERE migration_id = ?", (db.POSTER_MIGRATION_ID,))
        db.init_db()
        backups = [name for name in os.listdir(db.BACKUP_DIR) if name.endswith(".db")]
        self.assertTrue(backups, "Phase 4 startup migration must create a backup first")
        backup_path = os.path.join(db.BACKUP_DIR, backups[0])
        backup_conn = sqlite3.connect(backup_path)
        try:
            old_columns = {row[1] for row in backup_conn.execute("PRAGMA table_info(media_items)")}
            old_category = backup_conn.execute("SELECT content_category FROM media_items WHERE unique_key = 'startup-old'").fetchone()[0]
        finally:
            backup_conn.close()
        self.assertNotIn("poster_status", old_columns)
        self.assertEqual(old_category, "general_movie")
        with db.get_connection() as conn:
            new_columns = {row["name"] for row in conn.execute("PRAGMA table_info(media_items)")}
            migration = conn.execute("SELECT 1 FROM schema_migrations WHERE migration_id = ?", (db.POSTER_MIGRATION_ID,)).fetchone()
        self.assertIn("poster_status", new_columns)
        self.assertIsNotNone(migration)

    def test_forced_metadata_upsert_cannot_replace_manual_poster(self):
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO media_items (unique_key, file_name, title, content_category, created_at, updated_at, poster_url, local_poster_path, poster_status, poster_is_manual) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("locked-poster", "Movie.mkv", "Movie", "general_movie", "2026-01-01", "2026-01-01", "https://img.example/manual.jpg", "manual-cache.jpg", "manual", 1),
            )
        db.upsert_media_item({
            "unique_key": "locked-poster", "file_name": "Movie.mkv", "title": "Updated Metadata",
            "content_category": "general_movie", "has_metadata": 1,
            "poster_url": "https://img.example/automatic.jpg", "local_poster_path": "automatic-cache.jpg",
            "_force_meta": True,
        })
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT poster_url, local_poster_path, poster_is_manual, poster_status FROM media_items WHERE unique_key = ?",
                ("locked-poster",),
            ).fetchone()
        self.assertEqual(row["poster_url"], "https://img.example/manual.jpg")
        self.assertEqual(row["local_poster_path"], "manual-cache.jpg")
        self.assertEqual(row["poster_is_manual"], 1)
        self.assertEqual(row["poster_status"], "manual")
