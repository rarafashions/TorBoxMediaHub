"""Static contract checks for the v2 Discovery view and curation tools.

The v2 UI ships without a build step, so these tests guard the wiring between
``static/ui/home.js``, ``templates/home_v2.html`` and the existing REST surface.
They only assert presence and endpoint reuse: behaviour is covered by the
browser checks and by the API tests for each endpoint.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "home_v2.html"
SCRIPT = ROOT / "static" / "ui" / "home.js"
STYLES = ROOT / "static" / "ui" / "app.css"


class V2DiscoveryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8")
        cls.js = SCRIPT.read_text(encoding="utf-8")
        cls.css = STYLES.read_text(encoding="utf-8")

    def test_discovery_view_is_registered_in_navigation_and_router(self):
        for marker in (
            'data-view="discover"',
            "discover: renderDiscover",
            'discover: "Discovery"',
            "function renderDiscover(",
            "function ensureDiscoveryView(",
            "state.view === \"discover\"",
        ):
            with self.subTest(marker=marker):
                haystack = self.html if 'data-view="discover"' in marker else self.js
                self.assertIn(marker, haystack)

    def test_mainstream_discovery_reuses_feed_and_import_endpoints(self):
        for marker in (
            "/api/discover?",
            "/api/discover/search-cache",
            "/api/torbox/add-cached",
            'general_movie',
            'tv_show',
            'donghua',
            "Auto-import ke Library berjalan",
            "delete_remote_on_replace: true",
            "TMDb API Key",
            "Buka Pengaturan",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.js)

    def test_adult_tab_keeps_gate_and_uses_the_adult_surface(self):
        for marker in (
            "tbh_adult_v2_gate",
            "adultGateApproved",
            "approveAdultGate",
            "/api/adult/providers",
            "/api/adult/feed",
            "/api/adult/search",
            "/api/adult/detail",
            "/api/adult/streams",
            "/api/adult/webdl",
            '_roxxySource: "english_adult"',
            "100 GB per tautan",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.js)

    def test_detail_sheet_offers_manual_curation_actions(self):
        for marker in (
            "Edit metadata",
            "Klasifikasi",
            "Hapus fisik",
            "/manual",
            "/classification/preview",
            "/classification/apply",
            "/poster/manual",
            "/poster/automatic",
            "/delete-physical",
            "Tindakan ini tidak bisa dibatalkan",
            "delete_local: true",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.js)

    def test_discovery_styles_exist_and_stay_scoped(self):
        for marker in (
            ".discover-tabs",
            ".discover-tab.is-active",
            ".disc-detail__head",
            ".disc-video-card .card__frame",
            ".curate-grid",
            ".detail__extra--curation",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.css)


if __name__ == "__main__":
    unittest.main()