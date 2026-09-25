"""v61: admin test builder API - question picker filters, create/edit tests, safety rules, release time."""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V61TestBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v61-test.db'
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass
        os.environ.update({
            'DEV_SQLITE_PATH': str(cls.db), 'APP_ENV': 'development', 'DB_BACKEND': 'sqlite',
            'DEV_SHOW_OTP': 'true', 'PAYMENT_PROVIDER': 'development',
        })
        from fastapi.testclient import TestClient
        import app
        cls.client_ctx = TestClient(app.app)
        cls.client = cls.client_ctx.__enter__()
        r = cls.client.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'})
        assert r.status_code == 200, r.text
        cls.admin = {'Authorization': f"Bearer {r.json()['access_token']}"}
        r = cls.client.post('/api/v1/auth/register', json={
            'first_name': 'Stu', 'email': 'v61@example.com', 'mobile': '9000000061', 'password': 'StrongPass61!'})
        assert r.status_code == 200, r.text
        cls.student = {'Authorization': f"Bearer {r.json()['access_token']}"}
        # premium tests need an active plan: buy one through the development payment flow
        plan_id = cls.client.get('/api/v1/plans').json()[0]['id']
        order = cls.client.post('/api/v1/payments/order', headers=cls.student, json={'plan_id': plan_id})
        cls.client.post('/api/v1/payments/development/confirm', headers=cls.student, json={'order_id': order.json()['order_id'], 'method': 'card'})
        # import the user's real question file so the builder works with realistic data
        data = (BACKEND / 'sample_data' / 'QUESTION_1.docx').read_bytes()
        r = cls.client.post('/api/v1/admin/questions/import', headers=cls.admin, files={'file': ('q.docx', data)},
                            params={'subject_id': 'history', 'topic': 'Harappan Civilisation', 'dry_run': 'false',
                                    'status': 'published', 'difficulty': 'difficult'})
        assert r.json()['created'] == 10, r.text

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def qids(self, **params):
        r = self.client.get('/api/v1/admin/questions', headers=self.admin, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def body(self, **over):
        b = {'series_id': 'series1', 'title': 'Harappan Test 01', 'slug': 'harappan-test-01', 'description': None,
             'test_type': 'subject', 'duration_minutes': 20, 'total_marks': 20, 'negative_mark': 0.67,
             'access_type': 'premium', 'scheduled_at': None, 'status': 'published', 'question_ids': []}
        b.update(over)
        return b

    def test_1_picker_filters_and_import_order(self):
        rows = self.qids(subject='history', limit=50)
        self.assertEqual(len(rows), 10)
        # newest batch first and the file's own order kept inside it
        self.assertTrue(rows[0]['stem'].startswith('Consider the following statements regarding the Harappan Civilisation'))
        self.assertEqual(len(self.qids(subject='history', difficulty='difficult')), 10)
        self.assertEqual(len(self.qids(subject='history', difficulty='easy')), 0)
        self.assertEqual(len(self.qids(subject='history', limit=3)), 3)
        self.assertEqual(self.client.get('/api/v1/admin/questions', headers=self.admin, params={'limit': 999}).status_code, 422)

    def test_2_create_edit_publish_and_take_a_test(self):
        ids = [q['id'] for q in self.qids(subject='history', limit=50)]
        # publishing an empty test is refused, a draft is fine
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body()).status_code, 400)
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(status='draft'))
        self.assertEqual(r.status_code, 200, r.text)
        tid = r.json()['id']

        # add 10 questions (duplicates in the request are dropped) and publish
        r = self.client.put(f'/api/v1/admin/tests/{tid}', headers=self.admin,
                            json=self.body(question_ids=ids + ids[:2], total_marks=20))
        self.assertEqual(r.status_code, 200, r.text)
        detail = r.json()
        self.assertEqual((detail['total_questions'], len(detail['questions']), detail['status']), (10, 10, 'published'))
        self.assertEqual([q['id'] for q in detail['questions']], ids)
        self.assertEqual(detail['questions'][0]['marks'], 2)          # 20 marks / 10 questions
        self.assertEqual(detail['questions'][0]['negative_marks'], 0.67)
        self.assertEqual(self.client.get(f'/api/v1/admin/tests/{tid}', headers=self.admin).json()['attempt_count'], 0)

        listing = {t['id']: t for t in self.client.get('/api/v1/admin/tests', headers=self.admin).json()}
        self.assertEqual((listing[tid]['question_count'], listing[tid]['attempt_count'], listing[tid]['series_name']),
                         (10, 0, 'UPSC Prelims 2027'))

        # a student can start it and sees the multi-line question text
        r = self.client.post(f'/api/v1/tests/{tid}/start', headers=self.student)
        self.assertEqual(r.status_code, 200, r.text)
        first = r.json()['questions'][0]
        self.assertIn('\n', first['stem'])
        self.assertEqual(len(r.json()['questions']), 10)

        # with an attempt on record the questions/marking are locked, the title is not
        r = self.client.put(f'/api/v1/admin/tests/{tid}', headers=self.admin, json=self.body(question_ids=ids[:5], total_marks=10))
        self.assertEqual(r.status_code, 409)
        self.assertIn('attempt', r.json()['detail'])
        r = self.client.put(f'/api/v1/admin/tests/{tid}', headers=self.admin,
                            json=self.body(title='Harappan Test 01 (renamed)', question_ids=ids, total_marks=20))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['title'], 'Harappan Test 01 (renamed)')

    def test_3_validation_and_unique_slug(self):
        ids = [q['id'] for q in self.qids(subject='history', limit=3)]
        ok = self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(title='Same', slug='same-slug', question_ids=ids, total_marks=6)).json()
        again = self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(title='Same', slug='same-slug', question_ids=ids, total_marks=6))
        self.assertEqual(again.status_code, 200, again.text)
        self.assertNotEqual(ok['slug'], again.json()['slug'])
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(series_id='nope', question_ids=ids)).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(question_ids=['ghost'], status='draft')).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(status='live', question_ids=ids)).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(scheduled_at='not a date', question_ids=ids)).status_code, 400)
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.student, json=self.body(question_ids=ids)).status_code, 403)
        self.assertEqual(self.client.get('/api/v1/admin/tests/does-not-exist', headers=self.admin).status_code, 404)

    def test_4_release_time_is_enforced(self):
        ids = [q['id'] for q in self.qids(subject='history', limit=2)]
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json=self.body(title='Later', slug='later', question_ids=ids, total_marks=4, scheduled_at=soon))
        self.assertEqual(r.status_code, 200, r.text)
        tid = r.json()['id']
        started = self.client.post(f'/api/v1/tests/{tid}/start', headers=self.student)
        self.assertEqual(started.status_code, 403)
        self.assertIn('opens on', started.json()['detail'])
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        r = self.client.put(f'/api/v1/admin/tests/{tid}', headers=self.admin, json=self.body(title='Later', slug='later', question_ids=ids, total_marks=4, scheduled_at=past))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.post(f'/api/v1/tests/{tid}/start', headers=self.student).status_code, 200)

    def test_5_series_creation_handles_duplicates(self):
        b = {'name': 'Fresh Series', 'slug': 'upsc-prelims-2027', 'description': None, 'price_paise': 49900, 'validity_days': 90, 'status': 'published', 'featured': False}
        r = self.client.post('/api/v1/admin/series', headers=self.admin, json=b)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['slug'], 'upsc-prelims-2027-2')


if __name__ == '__main__':
    unittest.main()
