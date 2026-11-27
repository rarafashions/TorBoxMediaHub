import unittest

from classification_engine import classify_media, normalize_media_title
import indo_keyword_engine as indo_eng


class ClassificationGoldenTests(unittest.TestCase):
    def assert_classified(self, filename, expected, parent=""):
        result = classify_media(filename, parent)
        self.assertEqual(result["classification_status"], "classified", result)
        self.assertEqual(result["detected_category"], expected, result)
        return result

    def test_known_jav_code(self):
        result = self.assert_classified("[SomeGroup] IPX-123 1080p HEVC.mkv", "jav")
        self.assertEqual(result["detected_code"], "IPX-123")
        self.assertEqual(result["category_confidence"], 99)

    def test_fc2_code(self):
        result = self.assert_classified("FC2 PPV 1234567.mp4", "jav")
        self.assertEqual(result["detected_code"], "FC2-PPV-1234567")

    def test_bracketed_english_adult_studio(self):
        result = self.assert_classified("[Blacked] Riley Steele - The Wedding Singer.mp4", "english_adult")
        self.assertEqual(result["classification_method"], "explicit_adult_studio")

    def test_parenthesized_english_adult_studio(self):
        self.assert_classified("(Brazzers) Scene Title 2024.mp4", "english_adult")

    def test_anime_fansub(self):
        self.assert_classified("[SubsPlease] Anime Title - 03 [1080p].mkv", "anime")

    def test_anime_kana(self):
        self.assert_classified("進撃の巨人 第01話.mkv", "anime")

    def test_donghua_known_title(self):
        self.assert_classified("Soul Land S01E01 1080p.mkv", "donghua")

    def test_donghua_explicit_tag(self):
        self.assert_classified("Example Series Chinese Anime 01.mkv", "donghua")

    def test_japanese_release_parent_with_unknown_code_is_not_anime(self):
        result = classify_media(
            "hhd800.com@SNOS-038.mp4",
            "+++ [FHD] SNOS-038 AVデビュー1周年記念作品 最強ヒロインとSEXできるファン感謝祭",
        )
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertEqual(result["classification_method"], "unverified_code")

    def test_episodic_korean_dual_audio_needs_review(self):
        result = classify_media(
            "[INDEX] Solo Leveling - S02E01 [A5D2C79C].mkv",
            "[INDEX] Solo Leveling Season 2 [JP.BD][AV1][10Bit] (Dual Audio + Korean)",
        )
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertEqual(result["classification_method"], "ambiguous_episodic_language_hint")

    def test_tv_episode_structure(self):
        result = self.assert_classified("The Example Show S02E03 1080p.mkv", "tv_show")
        self.assertIn("season_episode_pattern", result["signals"])
        self.assertEqual(result["season_num"], 2)
        self.assertEqual(result["episode_num"], 3)

    def test_general_movie_with_year(self):
        self.assert_classified("The Example Movie 2024 1080p WEB-DL.mkv", "general_movie")

    def test_generic_code_is_not_assumed_jav(self):
        result = classify_media("ABC-123_Final_1080p.mp4")
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertIsNone(result["detected_category"])
        self.assertEqual(result["classification_method"], "unverified_code")

    def test_hanzi_alone_needs_review(self):
        result = classify_media("某个电影 2023.mp4")
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertIsNone(result["detected_category"])


    def test_brazzersexxtra_studio_alias_and_resolution_not_code(self):
        result = self.assert_classified(
            "brazzersexxtra.26.09.25.yasmina.khan.vips.in.the.club.480p.mp4",
            "english_adult",
        )
        self.assertNotEqual(result["detected_code"], "CLUB-480")

    def test_performer_like_name_without_year_needs_review(self):
        result = classify_media("Gabbie Carter.mp4")
        self.assertEqual(result["classification_status"], "needs_review")

    def test_unverified_jav_like_prefix_needs_review(self):
        result = classify_media("NGHJ-084.mp4")
        self.assertEqual(result["classification_status"], "needs_review")
        self.assertEqual(result["classification_method"], "unverified_code")

    def test_movie_title_with_year_is_classified(self):
        self.assert_classified("Love.2015.720p.BRRip.x264.AAC-ETRG.mp4", "general_movie")

    def test_uninformative_filename_needs_review(self):
        result = classify_media("video_001.mp4")
        self.assertEqual(result["classification_status"], "needs_review")

    def test_normalizer_preserves_meaningful_bracketed_title(self):
        self.assertEqual(normalize_media_title("[REC] 2007 1080p BluRay.mkv"), "REC")

    def test_normalizer_preserves_meaningful_suffix_after_year(self):
        filename = (
            "Apocalypse Z New World (2026) HQ HDRip - 1080p - x264 - "
            "[Tel + Tam + Hin + Eng] - (DD+5.1 - 640Kbps) - 3.3GB - ESub.mkv"
        )
        self.assertEqual(normalize_media_title(filename), "Apocalypse Z New World HQ")

    def test_normalizer_removes_release_tags(self):
        self.assertEqual(
            normalize_media_title("[SubsPlease] Anime.Title.S01E02.1080p.WEB-DL.mkv"),
            "Anime Title",
        )


class IndoKeywordPriorityTests(unittest.TestCase):
    """Indonesian regional vocabulary must win over generic English adult tags."""

    def test_selingkuh_alone_is_indo(self):
        verdict = indo_eng.classify_indo({"title": "lagi selingkuh laki nya nelpon Full"})
        self.assertTrue(verdict["is_indo"], verdict)
        self.assertIn("selingkuh", verdict["matched_keywords"])

    def test_selingkuh_variants_are_strong_signal(self):
        for title in (
            "Perselingkuhan di kontrakan",
            "Istri berselingkuh dengan tetangga",
            "Video selingkuhan Full",
        ):
            with self.subTest(title=title):
                self.assertTrue(indo_eng.classify_indo({"title": title})["is_indo"])

    def test_single_weak_supporting_word_still_needs_two_signals(self):
        verdict = indo_eng.classify_indo({"title": "Curhat malam minggu"})
        self.assertFalse(verdict["is_indo"], verdict)

    def test_classification_prefers_indo_over_english_adult_genres(self):
        result = classify_media(
            "lagi selingkuh laki nya nelpon full.mp4",
            normalized_title="lagi selingkuh laki nya nelpon Full",
            metadata={
                "genres": ["Teen", "Anal", "Asian", "Blowjob", "Bbw"],
                "description": "4:01 - Teen, Anal, Asian, Blowjob",
            },
        )
        self.assertEqual(result["classification_status"], "classified")
        self.assertEqual(result["detected_category"], "indo")
        self.assertEqual(result["classification_method"], "indo_keyword_classifier")

    def test_english_adult_without_indo_terms_keeps_its_category(self):
        result = classify_media("English Teen Anal Blowjob Scene.mp4")
        self.assertEqual(result["detected_category"], "english_adult")

    def test_two_supporting_clues_are_enough(self):
        verdict = indo_eng.classify_indo({"title": "Tante di Jakarta"})
        self.assertTrue(verdict["is_indo"], verdict)
        self.assertEqual(verdict["score"], 2)

    def test_single_supporting_clue_is_not_enough(self):
        verdict = indo_eng.classify_indo({"title": "Tante malam minggu"})
        self.assertFalse(verdict["is_indo"], verdict)

    def test_possessive_suffix_matches_regional_word(self):
        verdict = indo_eng.classify_indo({"title": "Tetangganya di kontrakan"})
        self.assertTrue(verdict["is_indo"], verdict)
        self.assertIn("tetangga", verdict["matched_keywords"])

    def test_filename_evidence_can_classify_indo(self):
        result = classify_media(
            "janda anak kos 720p.mp4",
            normalized_title="Generic Amateur Scene",
            metadata={"genres": ["Amateur"]},
        )
        self.assertEqual(result["classification_status"], "classified", result)
        self.assertEqual(result["detected_category"], "indo")
        self.assertEqual(result["classifier_version"], "d2-multicat-5")

    def test_neutral_filename_does_not_flip_english_adult(self):
        result = classify_media(
            "scene.2024.1080p.mp4",
            normalized_title="[Blacked] Riley Steele - The Wedding Singer",
        )
        self.assertEqual(result["detected_category"], "english_adult")


if __name__ == "__main__":
    unittest.main()
