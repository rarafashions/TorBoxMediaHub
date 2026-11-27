"""Guardrails for Roxxy download destinations and the stream-inspection cache."""
import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

import roxxy_download_engine as rde
import roxxy_module as rm


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.realpath(path)).rstrip("\\/")


class SafeDownloadDirTests(unittest.TestCase):
    def test_accepts_existing_absolute_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = rde._safe_download_dir(tmp)
            self.assertEqual(_normalized(resolved), _normalized(tmp))

    def test_rejects_empty_and_relative_paths(self):
        for value in ("", "   ", "Movies", os.path.join("relative", "dir")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rde._safe_download_dir(value)

    def test_rejects_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "does", "not", "exist")
            with self.assertRaises(ValueError):
                rde._safe_download_dir(missing)

    def test_rejects_path_inside_denied_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            protected = os.path.join(tmp, "protected")
            nested = os.path.join(protected, "nested")
            os.makedirs(nested)
            allowed = os.path.join(tmp, "allowed")
            os.makedirs(allowed)
            with patch.object(rde, "_download_denied_roots", return_value=[_normalized(protected)]):
                with self.assertRaises(ValueError):
                    rde._safe_download_dir(nested)
                self.assertEqual(_normalized(rde._safe_download_dir(allowed)), _normalized(allowed))

    def test_denied_roots_always_include_app_directory(self):
        app_dir = _normalized(os.path.dirname(os.path.abspath(rde.__file__)))
        self.assertIn(app_dir, rde._download_denied_roots())
        with self.assertRaises(ValueError):
            rde._safe_download_dir(os.path.join(app_dir, "templates"))

    @unittest.skipUnless(os.name == "nt", "Windows device namespace")
    def test_rejects_windows_device_namespace_prefix(self):
        with self.assertRaises(ValueError):
            rde._safe_download_dir("\\\\?\\C:\\Temp\\downloads")


class InspectCacheTests(unittest.TestCase):
    def setUp(self):
        rm._INSPECT_CACHE.clear()

    def tearDown(self):
        rm._INSPECT_CACHE.clear()

    @staticmethod
    def _fake_inspect(url: str) -> dict:
        return {"engine": "aria2c", "kind": "file", "content_type": "", "filename": "",
                "extension": ".mkv", "content_length": None, "http_status": 200, "url": url}

    def test_overflow_evicts_oldest_instead_of_flushing_cache(self):
        total = rm._INSPECT_CACHE_MAX + 5
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect) as inspect:
            for index in range(total):
                rm._resolve_remote_stream(f"https://cdn.example/v/{index}")

            self.assertEqual(inspect.call_count, total)
            self.assertEqual(len(rm._INSPECT_CACHE), rm._INSPECT_CACHE_MAX)

            oldest = hashlib.sha1(b"https://cdn.example/v/0").hexdigest()
            newest = hashlib.sha1(f"https://cdn.example/v/{total - 1}".encode()).hexdigest()
            self.assertNotIn(oldest, rm._INSPECT_CACHE)
            self.assertIn(newest, rm._INSPECT_CACHE)

            # A surviving entry is served from cache, so no extra inspection happens.
            rm._resolve_remote_stream(f"https://cdn.example/v/{total - 1}")
            self.assertEqual(inspect.call_count, total)

    def test_expired_entry_is_reinspected(self):
        url = "https://cdn.example/expired"
        key = hashlib.sha1(url.encode()).hexdigest()
        rm._INSPECT_CACHE[key] = (0.0, {"engine": "manual"})
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect) as inspect:
            result = rm._resolve_remote_stream(url)
        inspect.assert_called_once()
        self.assertEqual(result["engine"], "aria2c")

    def test_cache_hit_returns_a_copy(self):
        url = "https://cdn.example/copy"
        with patch("roxxy_download_engine._inspect", side_effect=self._fake_inspect):
            first = rm._resolve_remote_stream(url)
            first["engine"] = "tampered"
            second = rm._resolve_remote_stream(url)
        self.assertEqual(second["engine"], "aria2c")


if __name__ == "__main__":
    unittest.main()