"""Static checks for the legacy English Adult Discovery tab.

The UI has no build step, so these tests guard the contract between the tab and
the dedicated ``/api/adult/*`` surface: the panel must exist, every endpoint it
calls must be the new one, and the 18+ gate must stay a one-time prompt.
"""

import unittest
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "index.html"


class AdultDiscoveryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = TEMPLATE.read_text(encoding="utf-8")

    def test_adult_tab_and_panel_exist(self):
        for marker in (
            'id="disc-cat-english_adult"',
            'id="adult-panel"',
            'id="adult-grid"',
            'id="adult-detail"',
            'id="adult-paste-input"',
            'id="adult-detail-submit"',
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_adult_js_calls_only_new_surface(self):
        for endpoint in (
            "/api/adult/providers",
            "/api/adult/feed",
            "/api/adult/search",
            "/api/adult/link",
            "/api/adult/detail",
            "/api/adult/webdl",
            "/api/adult/streams",
            "/api/adult/download-status",
            "/api/adult/local-status",
        ):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, self.html)

    def test_detail_modal_has_rox_parity_sections(self):
        for marker in (
            'id="adult-detail-library-badge"',
            'id="adult-detail-webdl-hint"',
            'id="adult-cloud-status"',
            'id="adult-cloud-state"',
            'id="adult-cloud-queue"',
            'id="adult-stream-list"',
            'id="adult-streams-note"',
            'id="adult-download-list"',
            "Pilihan Sumber Stream",
            "Status TorBox Cloud",
            "Analisis ulang",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_local_engines_reuse_the_roxxy_pipeline(self):
        self.assertIn("/api/roxxy/analyze", self.html)
        self.assertIn("/api/roxxy/local/start", self.html)
        self.assertIn('_roxxySource: "english_adult"', self.html)

    def test_stream_markup_never_echoes_media_urls(self):
        # Only the display name and the suggested filename may reach the DOM;
        # signed provider URLs stay inside the analyse/local-start calls.
        for forbidden in ("${s.url", "${stream.url", "${analysis.url"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.html)

    def test_escape_closes_the_adult_modal(self):
        self.assertIn('document.getElementById("adult-detail").classList.contains("hidden")', self.html)

    def test_mainstream_discovery_route_is_unchanged(self):
        self.assertIn("/api/discover?", self.html)
        self.assertNotIn("category=english_adult", self.html)

    def test_adult_gate_is_remembered_in_local_storage(self):
        self.assertIn('localStorage.getItem("tbh_adult_gate")', self.html)
        self.assertIn('localStorage.setItem("tbh_adult_gate", "1")', self.html)

    def test_blocked_providers_are_labelled_honestly(self):
        for label in ("terblokir", "hanya pencarian", "tempel URL"):
            with self.subTest(label=label):
                self.assertIn(label, self.html)


if __name__ == "__main__":
    unittest.main()