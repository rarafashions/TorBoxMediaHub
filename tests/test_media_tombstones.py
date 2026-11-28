"""Tombstone history behind the "WAS IN LIBRARY" catalog badge.

Deleting a title now leaves a small identity row (title, file name, code,
series/season/episode) so the catalog can distinguish a title that was never
seen from one that was downloaded and later removed. No stream URL or media
row is retained.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module
import database_manager as db
import roxxy_module as rm


def _insert_media(**overrides):
    now = datetime.now().isoformat(timespec="seconds")
    payload = {
        "unique_key": "test:tombstone:1",
        "source_type": "cloud",
        "torbox_kind": "webdl",
        "torbox_item_id": "2436023",
        "file_name": "Massage Gone Squirt.mp4",
        "file_path": "",
        "file_size": 1024,
        "title": "Massage Gone Squirt",
        "content_category": "english_adult",
        "created_at": now,
        "updated_at": now,
    }
    payload.update(overrides)
    columns = ", ".join(payload)
    placeholders = ", ".join("?" for _ in payload)
    with db.get_connection() as conn:
        cursor = conn.execute(
            f"INSERT INTO media_items ({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def _tombstone_count() -> int:
    with db.get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM media_tombstones").fetchone()[0])


class TombstoneFixture(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self._saved_paths = (
            db.DB_DIR,
            db.DB_PATH,
            db.BACKUP_DIR,
            db.CACHE_DIR,
            db.PLAYLIST_DIR,
        )
        db.DB_DIR = os.path.join(self.temp_dir.name, "database")
        db.DB_PATH = os.path.join(db.DB_DIR, "torbox_media_hub.db")
        db.BACKUP_DIR = os.path.join(self.temp_dir.name, "backups")
        db.CACHE_DIR = os.path.join(self.temp_dir.name, "cache")
        db.PLAYLIST_DIR = os.path.join(self.temp_dir.name, "playlists")
        db.init_db()
        self.client = app_module.app.test_client()

    def tearDown(self):
        (
            db.DB_DIR,
            db.DB_PATH,
            db.BACKUP_DIR,
            db.CACHE_DIR,
            db.PLAYLIST_DIR,
        ) = self._saved_paths
        self.temp_dir.cleanup()


class PhysicalDeleteTombstoneTests(TombstoneFixture):
    def test_delete_physical_leaves_a_tombstone_and_bootstrap_marks(self):
        media_id = _insert_media()

        response = self.client.post(
            f"/api/media/{media_id}/delete-physical",
            json={"delete_local": False, "delete_remote": False},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["tombstoned"])
        self.assertEqual(_tombstone_count(), 1)

        bootstrap = self.client.get("/api/bootstrap?playlist_scope=all").get_json()
        marks = bootstrap["marks"]
        self.assertEqual(len(marks), 1)
        mark = marks[0]
        self.assertEqual(mark["title"], "Massage Gone Squirt")
        self.assertEqual(mark["content_category"], "english_adult")
        self.assertEqual(mark["delete_reason"], "physical_delete")
        self.assertNotIn("stream_url", mark)
        self.assertNotIn("file_path", mark)
        self.assertNotIn("torbox_item_id", mark)

    def test_redeleting_the_same_title_refreshes_one_row(self):
        media_id = _insert_media()
        self.client.post(
            f"/api/media/{media_id}/delete-physical",
            json={"delete_local": False, "delete_remote": False},
        )
        # A second download of the same title is imported again and deleted.
        media_id_2 = _insert_media(unique_key="test:tombstone:2")
        self.client.post(
            f"/api/media/{media_id_2}/delete-physical",
            json={"delete_local": False, "delete_remote": False},
        )

        self.assertEqual(_tombstone_count(), 1)
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT identity_key, delete_reason FROM media_tombstones"
            ).fetchone()
        self.assertEqual(row["identity_key"], "title:massage gone squirt")
        self.assertEqual(row["delete_reason"], "physical_delete")

    def test_episode_identity_keeps_each_episode_separate(self):
        db.add_media_tombstone(
            {
                "title": "Spare Me Great Lord S3 E16",
                "file_name": "spare me great lord s03e16.mp4",
                "series_title": "Spare Me Great Lord",
                "season_num": 3,
                "episode_num": 16,
                "content_category": "donghua",
            },
            reason="physical_delete",
        )
        db.add_media_tombstone(
            {
                "title": "Spare Me Great Lord S3 E17",
                "file_name": "spare me great lord s03e17.mp4",
                "series_title": "Spare Me Great Lord",
                "season_num": 3,
                "episode_num": 17,
                "content_category": "donghua",
            },
            reason="physical_delete",
        )

        marks = db.get_media_tombstone_marks()
        self.assertEqual(len(marks), 2)
        episodes = {(mark["season_num"], mark["episode_num"]) for mark in marks}
        self.assertEqual(episodes, {(3, 16), (3, 17)})

    def test_code_identity_wins_for_jav(self):
        db.add_media_tombstone(
            {
                "title": "Different title",
                "file_name": "NGHJ-084.mp4",
                "code_id": "nghj-084",
                "content_category": "jav",
            }
        )
        db.add_media_tombstone(
            {
                "title": "Another title",
                "file_name": "other.mkv",
                "code_id": "NGHJ-084",
                "content_category": "jav",
            }
        )
        self.assertEqual(_tombstone_count(), 1)

    def test_full_erase_clears_tombstones(self):
        db.add_media_tombstone({"title": "Removed Scene", "file_name": "removed.mp4"})
        self.assertEqual(_tombstone_count(), 1)

        result = db.erase_all_media_data()

        self.assertEqual(result["deleted_tombstones"], 1)
        self.assertEqual(_tombstone_count(), 0)

    def test_expired_rows_are_pruned_on_the_next_insert(self):
        db.add_media_tombstone({"title": "Ancient Removed Scene", "file_name": "ancient.mp4"})
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE media_tombstones SET deleted_at = ?",
                ((datetime.now() - timedelta(days=400)).isoformat(timespec="seconds"),),
            )
            conn.commit()

        db.add_media_tombstone({"title": "Fresh Removed Scene", "file_name": "fresh.mp4"})

        self.assertEqual(_tombstone_count(), 1)
        with db.get_connection() as conn:
            row = conn.execute("SELECT title FROM media_tombstones").fetchone()
        self.assertEqual(row["title"], "Fresh Removed Scene")

    def test_row_cap_keeps_the_newest_entries(self):
        for index in range(4):
            db.add_media_tombstone(
                {"title": f"Removed Scene Number {index}", "file_name": f"scene-{index}.mp4"}
            )
        with db.get_connection() as conn:
            removed = db._prune_media_tombstones(conn, max_rows=2)
            conn.commit()

        self.assertEqual(removed, 2)
        with db.get_connection() as conn:
            titles = [
                row["title"]
                for row in conn.execute("SELECT title FROM media_tombstones ORDER BY id ASC")
            ]
        self.assertEqual(titles, ["Removed Scene Number 2", "Removed Scene Number 3"])

    def test_maintenance_tombstones_missing_local_file(self):
        media_id = _insert_media(
            unique_key="test:local:missing",
            source_type="local",
            file_path=os.path.join(self.temp_dir.name, "never-existed.mp4"),
            title="Missing Local Scene",
            file_name="Missing Local Scene.mp4",
            content_category="indo",
        )

        result = db.run_db_maintenance()

        self.assertEqual(result["removed_missing_local_files"], 1)
        with db.get_connection() as conn:
            gone = conn.execute(
                "SELECT COUNT(*) FROM media_items WHERE id = ?", (media_id,)
            ).fetchone()[0]
            mark = conn.execute(
                "SELECT title, content_category, delete_reason FROM media_tombstones"
            ).fetchone()
        self.assertEqual(gone, 0)
        self.assertEqual(mark["title"], "Missing Local Scene")
        self.assertEqual(mark["content_category"], "indo")
        self.assertEqual(mark["delete_reason"], "missing_local_file")


class RoxxyBadgeStateTests(unittest.TestCase):
    def setUp(self):
        self.live = rm._build_library_index(
            [
                {
                    "id": 534,
                    "title": "Massage Gone Squirt",
                    "file_name": "Massage Gone Squirt.mp4",
                    "code_id": None,
                    "series_title": None,
                    "season_num": None,
                    "episode_num": None,
                }
            ]
        )
        self.live["tombstones"] = rm._build_library_index(
            [
                {
                    "title": "Ourdream | Busty Asian And Her Friend",
                    "file_name": "ourdream-busty-asian.mp4",
                    "code_id": None,
                    "series_title": None,
                    "season_num": None,
                    "episode_num": None,
                    "deleted_at": "2026-11-28T22:30:00",
                }
            ]
        )

    def test_live_match_wins_over_tombstone(self):
        state = rm._library_state_for_meta(
            {"id": "pornhub:1", "name": "Massage Gone Squirt"}, self.live
        )
        self.assertEqual(state["state"], "in_library")
        self.assertIsNone(state["was_match"])

    def test_removed_title_reports_was_in_library(self):
        state = rm._library_state_for_meta(
            {"id": "eporner:2", "name": "Ourdream | Busty Asian And Her Friend"},
            self.live,
        )
        self.assertEqual(state["state"], "was_in_library")
        self.assertIsNone(state["match"])
        self.assertEqual(
            state["was_match"]["deleted_at"], "2026-11-28T22:30:00"
        )

    def test_unknown_title_stays_not_in_library(self):
        state = rm._library_state_for_meta(
            {"id": "pornhub:3", "name": "Completely Different Scene"}, self.live
        )
        self.assertEqual(state["state"], "not_in_library")
        self.assertIsNone(state["was_match"])

    def test_meta_endpoint_exposes_was_flag(self):
        client = app_module.app.test_client()
        meta = {"id": "eporner:2", "name": "Ourdream | Busty Asian And Her Friend"}
        index = dict(self.live)
        with patch.object(
            rm, "_roxxy_get", return_value={"meta": meta}
        ), patch.object(rm, "_load_library_index", return_value=index):
            response = client.get("/api/roxxy/meta/movie/eporner:2")

        payload = response.get_json()["meta"]
        self.assertFalse(payload["in_library"])
        self.assertTrue(payload["was_in_library"])
        self.assertEqual(payload["library_state"], "was_in_library")
        self.assertEqual(
            payload["was_library_match"]["deleted_at"], "2026-11-28T22:30:00"
        )

    def test_series_episode_reports_was_in_library(self):
        meta = {
            "id": "series:spare-me-great-lord",
            "name": "Spare Me Great Lord",
            "type": "series",
            "videos": [
                {"id": "ep16", "title": "Spare Me Great Lord E16", "season": 1, "episode": 16},
                {"id": "ep17", "title": "Spare Me Great Lord E17", "season": 1, "episode": 17},
            ],
        }
        index = self.live
        index["tombstones"] = rm._build_library_index(
            [
                {
                    "title": "Spare Me Great Lord E16",
                    "file_name": "spare-me-great-lord-e16.mp4",
                    "code_id": None,
                    "series_title": "Spare Me Great Lord",
                    "season_num": 1,
                    "episode_num": 16,
                    "deleted_at": "2026-11-28T22:31:00",
                }
            ]
        )
        rm._annotate_episodes(meta, index)

        episodes = meta["series"]["seasons"][0]["episodes"]
        flags = {episode["episode"]: episode["library_state"] for episode in episodes}
        self.assertEqual(flags[16], "was_in_library")
        self.assertEqual(flags[17], "not_in_library")
        ep16 = next(episode for episode in episodes if episode["episode"] == 16)
        self.assertTrue(ep16["was_in_library"])
        self.assertEqual(ep16["was_library_match"]["deleted_at"], "2026-11-28T22:31:00")


class TombstoneGuardTests(unittest.TestCase):
    def test_missing_table_is_tolerated_by_readers(self):
        # Pre-tombstone snapshots must not break bootstrap or the adult hint.
        temp_dir = tempfile.TemporaryDirectory()
        saved = (db.DB_DIR, db.DB_PATH)
        try:
            db.DB_DIR = temp_dir.name
            db.DB_PATH = os.path.join(temp_dir.name, "old.db")
            conn = sqlite3.connect(db.DB_PATH)
            conn.close()
            self.assertEqual(db.get_media_tombstone_marks(), [])
            self.assertEqual(db.query_media_tombstones("anything"), [])
        finally:
            db.DB_DIR, db.DB_PATH = saved
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()