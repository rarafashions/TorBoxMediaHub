import unittest
from unittest.mock import patch
import subtitle_engine

class FakeResponse:
    def __init__(self, status=200, payload=None, content=b'1\n00:00:01,000 --> 00:00:02,000\nA valid subtitle line\n'):
        self.status_code = status
        self._payload = payload
        self.content = content
    def json(self):
        return self._payload

class Phase6B2Tests(unittest.TestCase):
    @patch('subtitle_engine.HTTP_SESSION.get')
    @patch('subtitle_engine._save_subtitle_bytes', return_value='/tmp/sub_id.srt')
    def test_opensubtitles_search_is_limited_to_requested_indonesian(self, save, get):
        get.side_effect = [
            FakeResponse(payload=[{'SubDownloadLink':'https://download.example/sub.srt'}]),
            FakeResponse(content=b'1\n00:00:01,000 --> 00:00:02,000\nIndonesian subtitle line\n')
        ]
        result = subtitle_engine._search_opensubtitles_rest('Example Film', 'unique', target_language='id')
        self.assertEqual(result, ('/tmp/sub_id.srt', 'id'))
        self.assertIn('sublanguageid-ind', get.call_args_list[0].args[0])
        self.assertNotIn('sublanguageid-eng', get.call_args_list[0].args[0])
        self.assertEqual(save.call_args.args[1], 'unique_id')

    @patch('subtitle_engine._search_opensubtitles_rest', return_value=('/tmp/en.srt', 'en'))
    def test_language_fetch_rejects_result_for_wrong_language(self, search):
        result = subtitle_engine.fetch_subtitle_for_language({'id': 99, 'title':'Example', 'source_type':'cloud'}, 'id')
        self.assertIsNone(result)

    def test_invalid_language_rejected(self):
        with self.assertRaises(ValueError):
            subtitle_engine.fetch_subtitle_for_language({'id':1}, 'ms')

if __name__ == '__main__':
    unittest.main()
