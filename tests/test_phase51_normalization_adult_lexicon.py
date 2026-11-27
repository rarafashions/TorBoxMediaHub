import unittest

import classification_engine as classifier
import metadata_engine as meta


class Phase51ReleaseNormalizationTests(unittest.TestCase):
    def test_removes_chained_language_suffix(self):
        self.assertEqual(
            meta.clean_search_title("Drive The Pretenders (2026) iTA-ENG.WEBDL.1080p.x264-Dr4gon.mkv"),
            "Drive The Pretenders",
        )
        self.assertEqual(
            meta.clean_search_title("Camp Miasma Adolescenza Sesso E Morte (2026) iTA-ENG.SPA.WEBDL.1080p.x264-Dr4gon.mkv"),
            "Camp Miasma Adolescenza Sesso E Morte",
        )

    def test_leading_year_is_removed_without_losing_title(self):
        self.assertEqual(
            meta.clean_search_title("2024 Angie Faith Fucking My Roommates Girlfriend Latest Leaked XXX 720p-XLeech.mp4"),
            "Angie Faith Fucking My Roommates Girlfriend Latest Leaked",
        )

    def test_short_rq_release_suffix_is_removed(self):
        self.assertEqual(
            meta.clean_search_title("Kendra Sunderland First Anal Experience rq.mp4"),
            "Kendra Sunderland First Anal Experience",
        )


class Phase51AdultLexiconTests(unittest.TestCase):
    def test_czech_street_and_harem_are_exact_adult_series_signals(self):
        for name in ("Czech Street - Model Name - Scene Title.mp4", "Czech Harem Model Name.mp4"):
            result = classifier.classify_media(name)
            self.assertEqual(result["detected_category"], "english_adult", name)
            self.assertEqual(result["classification_status"], "classified", name)

    def test_czech_alone_is_not_an_adult_signal(self):
        self.assertIsNone(meta._detect_english_adult_details("Czech Documentary 2024", "Czech Documentary 2024.mkv"))

    def test_existing_studio_signal_remains_supported(self):
        result = classifier.classify_media("Tushy - Kendra Sunderland - First Anal Experience.mp4")
        self.assertEqual(result["detected_category"], "english_adult")

    def test_unverified_code_is_not_forced_into_adult_category(self):
        result = classifier.classify_media("IMJO-015.mp4")
        self.assertNotEqual(result["detected_category"], "english_adult")
        self.assertEqual(result["normalized_title"], "IMJO-015")


if __name__ == "__main__":
    unittest.main()
