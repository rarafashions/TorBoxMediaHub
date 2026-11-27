import unittest
from unittest.mock import patch
import subtitle_engine as se


class Phase6B7RetrievalTests(unittest.TestCase):
    def test_ambiguous_filename_is_unknown(self):
        self.assertIsNone(se._detect_lang_from_name('Movie.Release.2026.srt'))
        self.assertEqual(se._detect_lang_from_name('Movie.2026.en.srt'), 'en')
        self.assertEqual(se._detect_lang_from_name('Movie.2026.id.srt'), 'id')

    @patch('subtitle_engine._save_subtitle_bytes', return_value='/tmp/cloud.srt')
    @patch('subtitle_engine.HTTP_SESSION.get')
    def test_cloud_index_downloads_only_requested_language(self, get, save):
        item = {'id': 1, 'unique_key': 'x', 'source_type': 'cloud', 'torbox_kind': 'torrent',
                'torbox_item_id': '77', 'file_name': 'Film.2026.mkv'}
        index = {('torrent', '77'): [
            {'file_id': '10', 'file_name': 'Film.2026.en.srt'},
            {'file_id': '11', 'file_name': 'Film.2026.id.srt'},
            {'file_id': '12', 'file_name': 'Film.2026.srt'},
        ]}
        class R:
            status_code = 200
            content = b'1\n00:00:01,000 --> 00:00:02,000\nA valid subtitle line\n'
            def json(self): return {'data': 'https://cdn.example/sub'}
        get.side_effect = [R(), R()]
        result = se.download_torbox_cloud_subtitle_from_index(item, 'key', index, 'id')
        self.assertEqual(result, ('/tmp/cloud.srt', 'id'))
        self.assertEqual(get.call_args_list[0].kwargs['params']['file_id'], '11')

    def test_cloud_rejects_unknown_language_filename(self):
        item = {'id': 1, 'source_type': 'cloud', 'torbox_kind': 'torrent',
                'torbox_item_id': '77', 'file_name': 'Film.2026.mkv'}
        index = {('torrent', '77'): [{'file_id': '12', 'file_name': 'Film.2026.srt'}]}
        with patch('subtitle_engine.HTTP_SESSION.get') as get:
            self.assertIsNone(se.download_torbox_cloud_subtitle_from_index(item, 'key', index, 'en'))
            get.assert_not_called()

    @patch('subtitle_engine.build_torbox_subtitle_index', return_value={})
    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/en.srt', 'en'))
    def test_movie_falls_back_to_requested_opensubtitles_language(self, search, index):
        item = {'id': 2, 'source_type': 'cloud', 'content_category': 'movie',
                'title': 'Example Film', 'file_name': 'Example.Film.mkv'}
        result = se.fetch_subtitle_for_language(item, 'en', torbox_api_key='key')
        self.assertEqual(result['subtitle_source'], 'opensubtitles')
        self.assertEqual(search.call_args.args[0], 'Example Film')
        self.assertEqual(search.call_args.kwargs['target_language'], 'en')

    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/id.srt', 'id'))
    def test_tv_anime_and_donghua_keep_episode_context(self, search):
        for category in ('tv_show', 'anime', 'donghua'):
            item = {'id': 3, 'source_type': 'local', 'content_category': category,
                    'series_title': 'Series Name', 'season_num': 2, 'episode_num': 4}
            # Avoid local scan in this unit test by supplying a non-local source.
            item['source_type'] = 'remote'
            result = se.fetch_subtitle_for_language(item, 'id')
            self.assertEqual(result['subtitle_lang'], 'id')
            self.assertEqual(search.call_args.args[0], 'Series Name')
            self.assertEqual(search.call_args.kwargs['season'], 2)
            self.assertEqual(search.call_args.kwargs['episode'], 4)
            search.reset_mock()

    @patch('subtitle_engine._search_subtitlecat_jav', return_value=('/tmp/jav_id.srt', 'id'))
    @patch('subtitle_engine._search_opensubtitles_rest')
    def test_jav_uses_code_id_and_language_targeted_subtitlecat(self, opensubs, subtitlecat):
        item = {'id': 4, 'source_type': 'remote', 'content_category': 'jav',
                'code_id': 'ABC-123', 'title': 'Unreliable Display Title'}
        result = se.fetch_subtitle_for_language(item, 'id')
        self.assertEqual(result['subtitle_source'], 'subtitlecat')
        self.assertEqual(subtitlecat.call_args.kwargs['target_language'], 'id')
        self.assertEqual(subtitlecat.call_args.args[0], 'ABC-123')
        opensubs.assert_not_called()

    @patch('subtitle_engine._search_subtitlecat_jav', return_value=None)
    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/jav_en.srt', 'en'))
    def test_jav_falls_back_to_opensubtitles_using_code_id(self, opensubs, subtitlecat):
        item = {'id': 5, 'source_type': 'remote', 'content_category': 'jav',
                'code_id': 'XYZ-321', 'title': 'Unreliable Title'}
        result = se.fetch_subtitle_for_language(item, 'en')
        self.assertEqual(result['subtitle_source'], 'opensubtitles')
        self.assertEqual(opensubs.call_args.args[0], 'XYZ-321')
        self.assertEqual(opensubs.call_args.kwargs['target_language'], 'en')

    @patch('subtitle_engine._search_subtitlecat_jav')
    @patch('subtitle_engine._search_opensubtitles_rest')
    def test_english_adult_still_skips_public_providers(self, opensubs, subtitlecat):
        item = {'id': 6, 'source_type': 'remote', 'content_category': 'english_adult', 'title': 'Adult title'}
        self.assertIsNone(se.fetch_subtitle_for_language(item, 'en'))
        opensubs.assert_not_called()
        subtitlecat.assert_not_called()

    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/en.srt', 'en'))
    def test_wrong_language_provider_result_is_rejected(self, search):
        item = {'id': 7, 'source_type': 'remote', 'content_category': 'uncategorized', 'title': 'Unknown'}
        self.assertIsNone(se.fetch_subtitle_for_language(item, 'id'))

    @patch('subtitle_engine._search_opensubtitles_rest')
    @patch('subtitle_engine.download_torbox_cloud_subtitle_from_index', return_value=('/tmp/cloud_id.srt', 'id'))
    @patch('subtitle_engine.build_torbox_subtitle_index', return_value={('torrent', '77'): []})
    def test_cloud_subtitle_precedes_external_provider(self, build_index, cloud_download, opensubs):
        item = {'id': 8, 'source_type': 'cloud', 'content_category': 'movie',
                'torbox_kind': 'torrent', 'torbox_item_id': '77', 'title': 'Film'}
        result = se.fetch_subtitle_for_language(item, 'id', torbox_api_key='key')
        self.assertEqual(result['subtitle_source'], 'torbox_cloud')
        cloud_download.assert_called_once()
        opensubs.assert_not_called()

    @patch('subtitle_engine._save_subtitle_bytes', return_value='/tmp/sc.srt')
    @patch('subtitle_engine.HTTP_SESSION.get')
    def test_subtitlecat_uses_only_explicit_requested_language(self, get, save):
        class Response:
            status_code = 200
            content = b'1\n00:00:01,000 --> 00:00:02,000\nSubtitle text long enough\n'
            def __init__(self, text=''): self.text = text
        get.side_effect = [
            Response('<table class="sub-table"><tbody><tr><td><a href="/detail/abc">ABC-123</a></td></tr></tbody></table>'),
            Response('<a class="green-link" href="/download-abc-en.srt">English</a><a class="green-link" href="/download-abc-id.srt">Indonesian</a>'),
            Response(),
        ]
        result = se._search_subtitlecat_jav('ABC-123', 'javtest', target_language='id')
        self.assertEqual(result, ('/tmp/sc.srt', 'id'))
        self.assertIn('download-abc-id.srt', get.call_args_list[2].args[0])

    @patch('subtitle_engine._search_subtitlecat_jav', return_value=None)
    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/jav.srt', 'en'))
    def test_jav_without_code_id_falls_back_to_title(self, opensubs, subtitlecat):
        item = {'id': 9, 'source_type': 'remote', 'content_category': 'jav',
                'title': 'Catalog Title', 'file_name': 'file.mkv'}
        result = se.fetch_subtitle_for_language(item, 'en')
        self.assertEqual(result['subtitle_source'], 'opensubtitles')
        self.assertEqual(opensubs.call_args.args[0], 'Catalog Title')
        subtitlecat.assert_not_called()


if __name__ == '__main__':
    unittest.main()
