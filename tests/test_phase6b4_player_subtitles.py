"""Regression tests for Phase 6B.4/6B.5 subtitle playback behavior."""
import tempfile
import unittest
from pathlib import Path

import dual_subtitle_engine as dual


class PlayerSubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.en = self.root / 'movie.en.srt'
        self.id = self.root / 'movie.id.srt'
        self.en.write_text('1\\n00:00:01,000 --> 00:00:02,000\\nEnglish\\n', encoding='utf-8')
        self.id.write_text('1\\n00:00:01,000 --> 00:00:02,000\\nIndonesia\\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def row(self, lang, path, embedded=False, verified=True):
        return {'language': lang, 'subtitle_path': str(path), 'is_embedded': embedded,
                'verified': verified}

    def test_external_tracks_order_english_then_indonesian(self):
        result = dual.external_playback_tracks([self.row('id', self.id), self.row('en', self.en)])
        self.assertEqual([r['language'] for r in result], ['en', 'id'])

    def test_embedded_english_loads_only_external_indonesian(self):
        result = dual.external_playback_tracks([
            self.row('en', 'embedded://stream/3', embedded=True), self.row('id', self.id)])
        self.assertEqual([r['language'] for r in result], ['id'])

    def test_embedded_indonesian_loads_only_external_english(self):
        result = dual.external_playback_tracks([
            self.row('id', 'embedded://stream/3', embedded=True), self.row('en', self.en)])
        self.assertEqual([r['language'] for r in result], ['en'])

    def test_both_embedded_load_no_external_files(self):
        result = dual.external_playback_tracks([
            self.row('en', 'embedded://stream/1', embedded=True),
            self.row('id', 'embedded://stream/2', embedded=True)])
        self.assertEqual(result, [])

    def test_unverified_or_missing_external_track_is_not_loaded(self):
        result = dual.external_playback_tracks([
            self.row('en', self.root / 'missing.srt'), self.row('id', self.id, verified=False)])
        self.assertEqual(result, [])

    def test_mpv_loads_both_external_tracks_and_enables_secondary_display(self):
        args = dual.mpv_subtitle_arguments([self.row('id', self.id), self.row('en', self.en)])
        self.assertIn(f'--sub-file={self.en}', args)
        self.assertIn(f'--sub-file={self.id}', args)
        self.assertIn('--sid=auto', args)
        self.assertIn('--secondary-sid=auto', args)
        self.assertIn('--sub-visibility=yes', args)
        self.assertIn('--secondary-sub-visibility=yes', args)

    def test_mpv_playlist_options_are_scoped_per_item(self):
        entries = [
            {'media_id': 10, 'target': '/media/one.mkv'},
            {'media_id': 20, 'target': '/media/two.mkv'},
        ]
        tracks = {
            10: [self.row('en', 'embedded://stream/1', embedded=True), self.row('id', self.id)],
            20: [self.row('en', self.en), self.row('id', self.id)],
        }
        args = dual.mpv_playlist_arguments(entries, tracks)
        self.assertEqual(args.count('--{'), 2)
        self.assertEqual(args.count('--}'), 2)
        first_open = args.index('--{')
        first_close = args.index('--}', first_open)
        second_open = args.index('--{', first_close + 1)
        second_close = args.index('--}', second_open)
        first_group = args[first_open:first_close]
        second_group = args[second_open:second_close]
        self.assertIn(f'--sub-file={self.id}', first_group)
        self.assertNotIn(f'--sub-file={self.en}', first_group)
        self.assertIn(f'--sub-file={self.en}', second_group)
        self.assertIn(f'--sub-file={self.id}', second_group)

    def test_cross_os_legacy_cache_path_resolves_under_current_cache(self):
        cache = self.root / 'cache'
        target = cache / 'subtitle' / 'movie.en.srt'
        target.parent.mkdir(parents=True)
        target.write_text('subtitle\n', encoding='utf-8')
        old_windows_path = r'C:\OldApp\cache\subtitle\movie.en.srt'
        resolved = dual.resolve_external_path(old_windows_path, str(cache))
        self.assertEqual(Path(resolved), target)

    def test_vlc_selects_only_external_complement(self):
        selected = dual.vlc_playback_track([
            self.row('en', 'embedded://stream/1', embedded=True), self.row('id', self.id)])
        self.assertEqual(selected['language'], 'id')


if __name__ == '__main__':
    unittest.main()
