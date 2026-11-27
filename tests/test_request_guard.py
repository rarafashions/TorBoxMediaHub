"""Cross-site write guard and the task-status telemetry cache."""
import unittest
from unittest.mock import patch

import app as tbh


class CrossSiteWriteGuardTests(unittest.TestCase):
    BASE = "http://127.0.0.1:5050/"

    def _guard(self, method="POST", **headers):
        with tbh.app.test_request_context("/api/x", method=method, base_url=self.BASE, headers=headers):
            return tbh._guard_cross_site_writes()

    def _is_blocked(self, result):
        return isinstance(result, tuple) and result[1] == 403

    def test_blocks_foreign_origin(self):
        self.assertTrue(self._is_blocked(self._guard(Origin="https://evil.example")))

    def test_blocks_foreign_referer_without_origin(self):
        self.assertTrue(self._is_blocked(self._guard(Referer="https://evil.example/page")))

    def test_blocks_cross_site_fetch_metadata(self):
        self.assertTrue(self._is_blocked(self._guard(**{"Sec-Fetch-Site": "cross-site"})))

    def test_blocks_null_origin(self):
        self.assertTrue(self._is_blocked(self._guard(Origin="null")))

    def test_allows_same_origin(self):
        self.assertIsNone(self._guard(Origin=self.BASE))

    def test_allows_same_origin_referer(self):
        self.assertIsNone(self._guard(Referer=self.BASE + "index.html"))

    def test_allows_same_origin_fetch_metadata(self):
        self.assertIsNone(self._guard(**{"Sec-Fetch-Site": "same-origin"}))

    def test_allows_requests_without_origin_headers(self):
        self.assertIsNone(self._guard())

    def test_does_not_guard_reads(self):
        self.assertIsNone(self._guard(method="GET", Origin="https://evil.example"))

    def test_does_not_guard_delete_preflight(self):
        self.assertIsNone(self._guard(method="OPTIONS", Origin="https://evil.example"))


class TaskTelemetryCacheTests(unittest.TestCase):
    def setUp(self):
        tbh._task_stats_cache.clear()
        self.addCleanup(tbh._task_stats_cache.clear)

    def test_reuses_value_within_ttl(self):
        with patch.object(tbh.db, "get_telemetry_stats", return_value={"total": 1}) as get:
            first = tbh._task_telemetry_stats()
            second = tbh._task_telemetry_stats()
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first, second)

    def test_refreshes_after_ttl(self):
        clock = [0.0]
        with patch.object(tbh.db, "get_telemetry_stats", return_value={"total": 1}) as get, \
                patch.object(tbh.time, "monotonic", side_effect=lambda: clock[0]):
            tbh._task_telemetry_stats()
            clock[0] = tbh._TASK_STATS_TTL_SECONDS + 0.1
            tbh._task_telemetry_stats()
        self.assertEqual(get.call_count, 2)

    def test_cache_is_scoped_to_the_status_endpoint(self):
        """Other endpoints must still read fresh stats after a write."""
        with patch.object(tbh.db, "get_telemetry_stats", return_value={"total": 1}) as get:
            tbh._task_telemetry_stats()
            tbh.db.get_telemetry_stats()
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()