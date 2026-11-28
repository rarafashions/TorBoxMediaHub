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

    def test_download_monitor_stays_visible_above_the_catalogue(self):
        # The modal closes, so progress + auto-add must live in the panel header
        # and never start hidden.
        self.assertIn('<div id="adult-downloads" class="bg-slate-950', self.html)
        self.assertNotIn('<div id="adult-downloads" class="hidden', self.html)
        self.assertIn('id="adult-download-empty"', self.html)
        self.assertIn("Monitor Unduhan", self.html)
        self.assertIn("Auto-add Library", self.html)
        self.assertIn("adultRecentImports", self.html)

    def test_catalogue_and_provider_health_are_split(self):
        for marker in (
            'id="adult-provider-health"',
            "refreshAdultProviderHealth",
            "xl:grid-cols-[minmax(0,1fr)_320px]",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_provider_page_metadata_fills_cover_and_synopsis(self):
        # Custom videos are usually unknown to TPDB, so the provider's own
        # OpenGraph/JSON-LD card is read first and TPDB only fills the gaps.
        for marker in (
            "page_meta",
            "adultPageMeta",
            "applyAdultVisualMeta",
            'id="adult-detail-synopsis"',
            "page.poster || meta.poster_url",
            "page.description || (found && meta.plot)",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)

    def test_header_stays_compact_and_paste_row_is_collapsible(self):
        # The discovery header was too tall: provider + mode + search + paste
        # each took a full row. Now paste is behind a toggle and the status
        # line shares its row with the visible count.
        for marker in (
            'id="adult-paste-toggle"',
            'id="adult-paste-row"',
            'aria-controls="adult-paste-row"',
            'id="adult-search-input"',
            'id="adult-visible-count"',
            "toggleAdultPasteRow",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.html)
        self.assertIn(
            'type="button" onclick="toggleAdultPasteRow()" id="adult-paste-toggle"',
            self.html,
        )

    def test_blocked_providers_are_labelled_honestly(self):
        for label in ("terblokir", "hanya pencarian", "tempel URL"):
            with self.subTest(label=label):
                self.assertIn(label, self.html)


if __name__ == "__main__":
    unittest.main()