"""v66: honest public results (aggregate, anonymous) and live site counts."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V66PublicResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v66-test.db'
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass
        os.environ.update({'DEV_SQLITE_PATH': str(cls.db), 'APP_ENV': 'development', 'DB_BACKEND': 'sqlite', 'DEV_SHOW_OTP': 'true', 'PAYMENT_PROVIDER': 'development'})
        from fastapi.testclient import TestClient
        import app
        cls.app = app
        cls.client_ctx = TestClient(app.app)
        cls.client = cls.client_ctx.__enter__()
        r = cls.client.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'})
        cls.admin = {'Authorization': f"Bearer {r.json()['access_token']}"}

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def make_test(self, title, access='free', n=4):
        qids = [q['id'] for q in self.client.get('/api/v1/admin/questions', headers=self.admin, params={'limit': n}).json()]
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json={
            'series_id': 'series1', 'title': title, 'slug': title.lower().replace(' ', '-'), 'description': None, 'test_type': 'subject',
            'duration_minutes': 10, 'total_marks': 2 * len(qids), 'negative_mark': 0.67, 'access_type': access, 'scheduled_at': None,
            'status': 'published', 'question_ids': qids})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()['id']

    def take(self, tid, n, answer='A'):
        r = self.client.post('/api/v1/auth/register', json={'first_name': f'Sec{n}', 'email': f'res{tid[:4]}{n}@example.com', 'mobile': f'9{tid[:3].replace("a","1").replace("b","2").replace("c","3").replace("d","4").replace("e","5").replace("f","6")}0{n:05d}', 'password': 'StrongPass66!'})
        self.assertEqual(r.status_code, 200, r.text)
        h = {'Authorization': f"Bearer {r.json()['access_token']}"}
        st = self.client.post(f'/api/v1/tests/{tid}/start', headers=h).json()
        if answer:
            for q in st['questions']:
                self.client.post(f"/api/v1/attempts/{st['attempt_id']}/questions/{q['id']}/answer", headers=h, json={'selected_option': answer, 'time_spent_seconds': 3})
        self.assertEqual(self.client.post(f"/api/v1/attempts/{st['attempt_id']}/submit", headers=h).status_code, 200)

    def test_1_empty_site_shows_nothing_invented(self):
        r = self.client.get('/api/v1/public/results').json()
        self.assertEqual((r['items'], r['overall']['tests'], r['overall']['participants'], r['overall']['avg_percent']), ([], 0, 0, None))

    def test_2_results_need_enough_participants_and_hide_names(self):
        tid = self.make_test('Result sampler')
        for n in range(1, 5):                                           # 4 students: below the minimum of 5
            self.take(tid, n)
        self.assertEqual(self.client.get('/api/v1/public/results').json()['items'], [])
        self.take(tid, 5, answer=None)                                  # a fifth student, who answers nothing
        res = self.client.get('/api/v1/public/results').json()
        self.assertEqual(len(res['items']), 1)
        item = res['items'][0]
        self.assertEqual((item['title'], item['participants']), ('Result sampler', 5))
        self.assertGreater(item['total_marks'], 0)
        self.assertGreaterEqual(item['top_score'], item['avg_score'])
        self.assertEqual(res['overall']['participants'], 5)
        self.assertEqual(res['overall']['top_percent'], item['top_percent'])
        # no personal data anywhere in the response
        text = self.client.get('/api/v1/public/results').text.lower()
        for word in ('sec1', 'email', '@example', 'user_id', 'mobile', 'first_name'):
            self.assertNotIn(word, text)
        # an unfinished attempt is not a result
        tid2 = self.make_test('Unfinished test')
        for n in range(1, 6):
            r = self.client.post('/api/v1/auth/register', json={'first_name': f'U{n}', 'email': f'unf{n}@example.com', 'mobile': f'9222200{n:03d}', 'password': 'StrongPass66!'})
            h = {'Authorization': f"Bearer {r.json()['access_token']}"}
            self.client.post(f'/api/v1/tests/{tid2}/start', headers=h)
        titles = [i['title'] for i in self.client.get('/api/v1/public/results').json()['items']]
        self.assertNotIn('Unfinished test', titles)

    def test_3_site_counts_are_live(self):
        before = self.client.get('/api/v1/public/stats').json()
        self.assertEqual({'questions', 'tests', 'series', 'pyqs', 'by_tier'}, set(before))
        self.client.post('/api/v1/admin/questions', headers=self.admin, json={
            'subject_id': 'polity', 'topic_id': None, 'stem': 'A brand new question?', 'option_a': 'a', 'option_b': 'b', 'option_c': 'c', 'option_d': 'd',
            'correct_option': 'A', 'explanation': None, 'difficulty': 'easy', 'upsc_year': None, 'status': 'published', 'source': None})
        self.client.post('/api/v1/admin/questions', headers=self.admin, json={
            'subject_id': 'polity', 'topic_id': None, 'stem': 'A draft question?', 'option_a': 'a', 'option_b': 'b', 'option_c': 'c', 'option_d': 'd',
            'correct_option': 'A', 'explanation': None, 'difficulty': 'easy', 'upsc_year': None, 'status': 'draft', 'source': None})
        after = self.client.get('/api/v1/public/stats').json()
        self.assertEqual(after['questions'], before['questions'] + 1)      # the draft is not counted


if __name__ == '__main__':
    unittest.main()
