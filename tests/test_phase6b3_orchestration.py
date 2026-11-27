import unittest
import tempfile
import os
import database_manager as db
import dual_subtitle_engine as dual


class Phase6B3PlanningTests(unittest.TestCase):
    def test_neither_language_requests_both_when_indonesian_enabled(self):
        self.assertEqual(dual.plan_missing_languages([], True), ['en', 'id'])

    def test_english_present_requests_indonesian_only(self):
        self.assertEqual(dual.plan_missing_languages([{'language': 'en'}], True), ['id'])

    def test_indonesian_present_requests_english_only(self):
        self.assertEqual(dual.plan_missing_languages([{'language': 'id'}], True), ['en'])

    def test_both_present_requests_nothing(self):
        found = [{'language': 'en'}, {'language': 'id'}]
        self.assertEqual(dual.plan_missing_languages(found, True), [])

    def test_disabled_indonesian_never_requests_it(self):
        self.assertEqual(dual.plan_missing_languages([], False), ['en'])
        self.assertEqual(dual.plan_missing_languages([{'language': 'en'}], False), [])

    def test_status_semantics_remain_compatible_when_optional_language_disabled(self):
        self.assertEqual(dual.subtitle_status([{'language': 'en'}], False), 'Complete')


class Phase6B3PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'test.db')
        os.makedirs(os.path.dirname(db.DB_PATH), exist_ok=True)
        with db.get_connection() as conn:
            conn.execute('CREATE TABLE subtitle_preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.commit()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def test_preferences_default_to_indonesian_enabled(self):
        prefs = db.get_subtitle_preferences()
        self.assertTrue(prefs['english_required'])
        self.assertTrue(prefs['indonesian_enabled'])

    def test_indonesian_preference_can_be_disabled_and_reenabled(self):
        self.assertFalse(db.save_subtitle_preferences(False)['indonesian_enabled'])
        self.assertTrue(db.save_subtitle_preferences(True)['indonesian_enabled'])


if __name__ == '__main__':
    unittest.main()
