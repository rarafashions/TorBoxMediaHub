"""Route split: UI v2 is the default at ``/``; the legacy UI stays at ``/legacy``.

These checks only guard the three HTML entry points and the legacy Discovery
deep link. API behaviour is covered by the endpoint-specific suites.
"""

import unittest
from urllib.parse import parse_qs, urlparse

import app as tbh


class UiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = tbh.app.test_client()

    def test_root_serves_v2(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('data-view="discover"', body)
        self.assertIn('id="v2-main"', body)
        self.assertNotIn('id="view-library"', body)

    def test_v2_alias_still_serves_v2(self):
        response = self.client.get("/v2")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('data-view="discover"', body)
        self.assertIn('id="v2-main"', body)

    def test_legacy_route_serves_old_ui(self):
        response = self.client.get("/legacy")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="view-library"', body)
        self.assertIn('id="tab-discover"', body)
        self.assertNotIn('data-view="discover"', body)

    def test_legacy_ui_links_back_to_v2(self):
        body = self.client.get("/legacy").get_data(as_text=True)
        self.assertIn('href="/" class="px-3 py-1.5 rounded-md text-xs font-semibold bg-emerald-700/90', body)

    def test_legacy_discovery_deeplink_redirects(self):
        response = self.client.get("/?discover=1")
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers["Location"])
        self.assertEqual(location.path, "/legacy")
        self.assertEqual(parse_qs(location.query).get("discover"), ["1"])

    def test_legacy_discovery_deeplink_serves_old_ui(self):
        response = self.client.get("/legacy?discover=1")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn('id="view-library"', body)


if __name__ == "__main__":
    unittest.main()