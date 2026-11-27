import os
import tempfile
import unittest
from unittest.mock import patch

import dual_subtitle_engine as engine
import database_manager as db
import sqlite3


class DualSubtitleEngineTests(unittest.TestCase):
    def test_language_normalization(self):
        self.assertEqual(engine.normalize_language('eng'), 'en')
        self.assertEqual(engine.normalize_language('id-ID'), 'id')
        self.assertIsNone(engine.normalize_language('ms'))

    def test_local_sidecars_detect_only_matching_video_and_target_languages(self):
        with tempfile.TemporaryDirectory() as folder:
            video = os.path.join(folder, 'Example.Movie.mkv')
            open(video, 'wb').close()
            for name in ('Example.Movie.en.srt', 'Example.Movie.id.srt', 'Example.Movie.ms.srt', 'Other.en.srt'):
                with open(os.path.join(folder, name), 'wb') as f:
                    f.write(b'1\n00:00:01,000 --> 00:00:02,000\nSubtitle line\n')
            rows = engine.scan_local_sidecars(video)
            self.assertEqual({r['language'] for r in rows}, {'en', 'id'})

    def test_missing_languages_and_status(self):
        only_id = [{'language': 'id', 'verified': True}]
        self.assertEqual(engine.missing_languages(only_id), ['en'])
        self.assertEqual(engine.subtitle_status(only_id), 'Partial')
        only_en = [{'language': 'en', 'verified': True}]
        self.assertEqual(engine.missing_languages(only_en), ['id'])
        self.assertEqual(engine.subtitle_status(only_en), 'Ready')
        both = only_en + only_id
        self.assertEqual(engine.missing_languages(both), [])
        self.assertEqual(engine.subtitle_status(both), 'Complete')
        self.assertEqual(engine.subtitle_status(only_en, indonesian_enabled=False), 'Complete')

    def test_additive_migration_preserves_legacy_subtitle_and_copies_language(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE media_items (id INTEGER PRIMARY KEY, has_subtitle INTEGER, subtitle_path TEXT, subtitle_lang TEXT, subtitle_source TEXT, created_at TEXT, updated_at TEXT)")
        conn.execute("CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO media_items VALUES (1,1,'/media/movie.id.srt','id','local_sidecar','now','now')")
        db.migrate_dual_subtitle_schema(conn)
        row = conn.execute("SELECT language,subtitle_path FROM media_subtitles WHERE media_id=1").fetchone()
        self.assertEqual((row['language'], row['subtitle_path']), ('id','/media/movie.id.srt'))
        legacy = conn.execute("SELECT subtitle_path,subtitle_lang FROM media_items WHERE id=1").fetchone()
        self.assertEqual((legacy['subtitle_path'],legacy['subtitle_lang']), ('/media/movie.id.srt','id'))
        db.migrate_dual_subtitle_schema(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_subtitles").fetchone()[0], 1)
        conn.close()

    @patch('dual_subtitle_engine.shutil.which', return_value='ffprobe')
    @patch('dual_subtitle_engine.subprocess.run')
    def test_embedded_scan_returns_both_target_languages(self, run, _which):
        class Proc:
            returncode = 0
            stdout = '{"streams":[{"index":2,"tags":{"language":"eng"}},{"index":3,"tags":{"language":"ind"}},{"index":4,"tags":{"language":"jpn"}}]}'
        run.return_value = Proc()
        rows = engine.scan_embedded_tracks('sample.mkv')
        self.assertEqual([r['language'] for r in rows], ['en', 'id'])
        self.assertEqual([r['stream_index'] for r in rows], [2, 3])


if __name__ == '__main__':
    unittest.main()
