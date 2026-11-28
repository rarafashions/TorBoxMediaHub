"""Static contract checks for the "WAS IN LIBRARY" badge.

The badge must appear on every surface that already shows "IN LIBRARY":
Roxxy catalog/detail, the adult detail modal (v2 + legacy), and Discovery
(v2 + legacy). These checks only assert wiring; server behaviour lives in
``tests/test_media_tombstones.py``.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME_JS = ROOT / "static" / "ui" / "home.js"
APP_CSS = ROOT / "static" / "ui" / "app.css"
ROXXY = ROOT / "templates" / "roxxy.html"
LEGACY = ROOT / "templates" / "index.html"


class WasInLibraryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = HOME_JS.read_text(encoding="utf-8")
        cls.css = APP_CSS.read_text(encoding="utf-8")
        cls.roxxy = ROXXY.read_text(encoding="utf-8")
        cls.legacy = LEGACY.read_text(encoding="utf-8")

    def test_v2_reads_marks_from_bootstrap(self):
        for marker in (
            "marks: []",
            "state.marks = Array.isArray(data.marks) ? data.marks : []",
            "discoveryTombstoneIndex",
            "discoverTombstoneMatch",
            "discoveryBadgeNode",
            "wasLibraryBadgeNode",
            "WAS IN LIBRARY",
            "Pernah ada di Library, sudah dihapus",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.js)

    def test_v2_badge_styles_exist(self):
        for marker in (".card__lib-badge--was", ".tag--was"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.css)

    def test_roxxy_surfaces_show_the_removed_state(self):
        for marker in (
            "was_in_library",
            "was_library_match",
            "WAS IN LIBRARY",
            "Pernah ada di Library, sudah dihapus",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.roxxy)

    def test_legacy_surfaces_show_the_removed_state(self):
        for marker in (
            "allTombstones",
            "findTombstoneLibraryItems",
            "was_in_library",
            "WAS IN LIBRARY",
            "Pernah ada di Library, sudah dihapus",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.legacy)


if __name__ == "__main__":
    unittest.main()