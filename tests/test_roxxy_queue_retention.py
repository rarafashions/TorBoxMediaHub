"""Batas riwayat download lokal, pembersihan hint impor, dan cache indeks Library."""
import unittest
from unittest.mock import patch

import app as app_module
import roxxy_download_engine as engine
import roxxy_module as rm


def _row(**overrides):
    base = {
        "id": 1,
        "title": "",
        "file_name": "",
        "parent_name": "",
        "source_type": "cloud",
        "content_category": "uncategorized",
        "has_metadata": 1,
        "has_subtitle": 0,
        "season_num": None,
        "episode_num": None,
        "code_id": None,
    }
    base.update(overrides)
    return base


def _job(job_id, status="completed", created_at="2026-11-27T09:00:00", library_status=None):
    return {
        "id": job_id,
        "status": status,
        "created_at": created_at,
        "library_status": library_status,
    }


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _FakeCursor(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class JobHistoryPruneTests(unittest.TestCase):
    def setUp(self):
        self._saved_jobs = dict(engine._jobs)
        engine._jobs.clear()

    def tearDown(self):
        engine._jobs.clear()
        engine._jobs.update(self._saved_jobs)

    def _add(self, job_id, **kwargs):
        engine._jobs[job_id] = _job(job_id, **kwargs)

    def test_trims_oldest_finished_jobs_beyond_limit(self):
        for index in range(5):
            self._add(f"old{index}", created_at=f"2026-11-27T09:0{index}:00")
        with engine._lock:
            removed = engine._prune_job_history_locked(keep=2)
        self.assertEqual(removed, 3)
        self.assertEqual(set(engine._jobs), {"old3", "old4"})

    def test_active_downloads_are_never_removed(self):
        self._add("done", created_at="2026-11-27T09:00:00")
        self._add("run", status="downloading", created_at="2026-11-27T09:01:00")
        self._add("queued", status="queued", created_at="2026-11-27T09:02:00")
        with engine._lock:
            removed = engine._prune_job_history_locked(keep=0)
        self.assertEqual(removed, 1)
        self.assertEqual(set(engine._jobs), {"run", "queued"})

    def test_completed_job_with_pending_library_task_is_kept(self):
        self._add("libqueued", library_status="queued", created_at="2026-11-27T09:00:00")
        self._add("libreg", library_status="registering", created_at="2026-11-27T09:01:00")
        with engine._lock:
            removed = engine._prune_job_history_locked(keep=0)
        self.assertEqual(removed, 0)
        self.assertEqual(len(engine._jobs), 2)

    def test_clear_endpoint_keeps_active_jobs(self):
        self._add("done", created_at="2026-11-27T09:00:00")
        self._add("failed", status="failed", created_at="2026-11-27T09:01:00")
        self._add("run", status="downloading", created_at="2026-11-27T09:02:00")
        response = app_module.app.test_client().post("/api/roxxy/local/clear")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["removed"], 2)
        self.assertEqual(payload["jobs"], 1)
        self.assertEqual(set(engine._jobs), {"run"})

    def test_clear_endpoint_with_empty_history_is_a_no_op(self):
        response = app_module.app.test_client().post("/api/roxxy/local/clear")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["removed"], 0)


class ImportHintCleanupTests(unittest.TestCase):
    def setUp(self):
        app_module._TORBOX_IMPORT_HINTS.clear()

    def tearDown(self):
        app_module._TORBOX_IMPORT_HINTS.clear()

    def test_hints_are_dropped_after_import(self):
        app_module._TORBOX_IMPORT_HINTS["webdl:1"] = {"metadata": {}}
        app_module._TORBOX_IMPORT_HINTS["webdl:2"] = {"metadata": {}}
        app_module._forget_import_hints({"webdl:1"})
        self.assertEqual(set(app_module._TORBOX_IMPORT_HINTS), {"webdl:2"})

    def test_single_key_and_empty_input_are_supported(self):
        app_module._TORBOX_IMPORT_HINTS["torrent:9"] = {"metadata": {}}
        app_module._forget_import_hints("torrent:9")
        app_module._forget_import_hints([])
        self.assertEqual(app_module._TORBOX_IMPORT_HINTS, {})


class LibraryIndexCacheTests(unittest.TestCase):
    def setUp(self):
        self._saved_ttl = rm._LIBRARY_INDEX_TTL_SECONDS
        rm.invalidate_library_index()

    def tearDown(self):
        rm._LIBRARY_INDEX_TTL_SECONDS = self._saved_ttl
        rm.invalidate_library_index()

    def _rows(self):
        return [
            _row(
                id=534,
                title="Massage Gone Squirt",
                file_name="Massage Gone Squirt.mp4",
                content_category="indo",
            )
        ]

    def test_repeated_requests_reuse_one_index_build(self):
        with patch.object(
            rm.db, "get_connection", side_effect=lambda: _FakeConnection(self._rows())
        ) as get_connection:
            first = rm._load_library_index()
            second = rm._load_library_index()
        self.assertIs(first, second)
        self.assertEqual(get_connection.call_count, 1)

    def test_invalidate_forces_a_rebuild(self):
        with patch.object(
            rm.db, "get_connection", side_effect=lambda: _FakeConnection(self._rows())
        ) as get_connection:
            rm._load_library_index()
            rm.invalidate_library_index()
            rm._load_library_index()
        self.assertEqual(get_connection.call_count, 2)

    def test_zero_ttl_rebuilds_on_every_call(self):
        rm._LIBRARY_INDEX_TTL_SECONDS = 0.0
        with patch.object(
            rm.db, "get_connection", side_effect=lambda: _FakeConnection(self._rows())
        ) as get_connection:
            rm._load_library_index()
            rm._load_library_index()
        self.assertEqual(get_connection.call_count, 2)

    def test_cached_index_still_matches_titles(self):
        with patch.object(
            rm.db, "get_connection", side_effect=lambda: _FakeConnection(self._rows())
        ):
            index = rm._load_library_index()
        match = rm._library_match_for_meta({"name": "Massage Gone Squirt"}, index)
        self.assertIsNotNone(match)
        self.assertEqual(match["id"], 534)


if __name__ == "__main__":
    unittest.main()