"""v63: tag questions with their UPSC exam year on import; public previous-year-question feed."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V63PyqTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v63-test.db'
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass
        os.environ.update({'DEV_SQLITE_PATH': str(cls.db), 'APP_ENV': 'development', 'DB_BACKEND': 'sqlite',
                           'DEV_SHOW_OTP': 'true', 'PAYMENT_PROVIDER': 'development'})
        from fastapi.testclient import TestClient
        import app
        cls.client_ctx = TestClient(app.app)
        cls.client = cls.client_ctx.__enter__()
        r = cls.client.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'})
        cls.admin = {'Authorization': f"Bearer {r.json()['access_token']}"}
        # a fresh install already contains a few sample questions with an exam year
        base = cls.client.get('/api/v1/public/pyqs').json()
        cls.base_total, cls.base_years = base['total'], base['years']
        cls.base_polity = cls.client.get('/api/v1/public/pyqs', params={'subject': 'polity'}).json()['total']

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def upload(self, name, data, **params):
        return self.client.post('/api/v1/admin/questions/import', headers=self.admin, params=params, files={'file': (name, data)})

    def test_1_docx_import_with_year_and_public_feed(self):
        data = (BACKEND / 'sample_data' / 'QUESTION_1.docx').read_bytes()
        r = self.upload('q.docx', data, subject_id='history', topic='Harappan Civilisation', year='2019', status='published', dry_run='false')
        self.assertEqual(r.json()['created'], 10, r.text)
        feed = self.client.get('/api/v1/public/pyqs').json()                            # no login needed
        self.assertEqual(feed['total'], self.base_total + 10)
        self.assertIn(2019, feed['years'])
        self.assertIn('history', [s['id'] for s in feed['subjects']])
        first = self.client.get('/api/v1/public/pyqs', params={'year': 2019}).json()['items'][0]
        self.assertEqual(first['upsc_year'], 2019)
        self.assertIn(first['correct_option'], 'ABCD')
        self.assertTrue(first['explanation'])
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'year': 1999}).json()['total'], 0)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'year': 2019, 'subject': 'polity'}).json()['total'], 0)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'search': 'Rakhigarhi'}).json()['total'] > 0, True)
        self.assertEqual(len(self.client.get('/api/v1/public/pyqs', params={'limit': 3}).json()['items']), 3)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'limit': 3}).json()['total'], self.base_total + 10)
        # the student PYQ page (question bank filtered by year) sees them too
        stu = self.client.post('/api/v1/auth/register', json={'first_name': 'P', 'email': 'v63@example.com', 'mobile': '9000000063', 'password': 'StrongPass63!'}).json()
        rows = self.client.get('/api/v1/student/questions', params={'year': 2019}, headers={'Authorization': f"Bearer {stu['access_token']}"}).json()
        self.assertEqual(len(rows), 10)

    def test_2_untagged_and_draft_questions_stay_out(self):
        r = self.upload('a.csv', b'Question,A,B,C,D,Answer,Subject\n"Untagged one","a","b","c","d","A","polity"\n"Draft PYQ","a","b","c","d","B","polity"\n',
                        status='published', dry_run='false')
        self.assertEqual(r.json()['created'], 2, r.text)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'subject': 'polity'}).json()['total'], self.base_polity)   # no year
        r = self.upload('b.csv', b'Question,A,B,C,D,Answer,Subject,Year\n"Tagged draft","a","b","c","d","C","polity","2021"\n', status='draft', dry_run='false')
        self.assertEqual(r.json()['created'], 1)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'subject': 'polity'}).json()['total'], self.base_polity)   # draft
        self.upload('c.csv', b'Question,A,B,C,D,Answer,Subject,Year\n"Tagged live","a","b","c","d","C","polity","2022"\n', status='published', dry_run='false')
        feed = self.client.get('/api/v1/public/pyqs', params={'subject': 'polity'}).json()
        self.assertEqual(feed['total'], self.base_polity + 1)
        self.assertEqual(self.client.get('/api/v1/public/pyqs', params={'year': 2022}).json()['items'][0]['stem'], 'Tagged live')
        self.assertEqual(feed['years'], sorted(set(feed['years']), reverse=True))

    def test_3_year_validation(self):
        data = (BACKEND / 'sample_data' / 'QUESTION_1.docx').read_bytes()
        self.assertEqual(self.upload('q.docx', data, subject_id='history', year='1850').status_code, 422)
        r = self.upload('bad.csv', b'Question,A,B,C,D,Answer,Subject,Year\n"Bad year row","a","b","c","d","A","polity","last year"\n', dry_run='true')
        self.assertEqual(r.json()['flagged'], 1)
        self.assertIn('year', r.json()['errors'][0]['problems'][0])


if __name__ == '__main__':
    unittest.main()
