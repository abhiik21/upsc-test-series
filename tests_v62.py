"""v62: premium tests need an active plan; free tests stay open; staff can preview."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V62AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v62-test.db'
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
        cls.admin = {'Authorization': f"Bearer {r.json()['access_token']}"}
        qids = [q['id'] for q in cls.client.get('/api/v1/admin/questions', headers=cls.admin, params={'limit': 2}).json()]
        base = {'series_id': 'series1', 'description': None, 'test_type': 'subject', 'duration_minutes': 10, 'total_marks': 4,
                'negative_mark': 0.67, 'scheduled_at': None, 'status': 'published', 'question_ids': qids}
        cls.free_id = cls.client.post('/api/v1/admin/tests', headers=cls.admin, json={**base, 'title': 'Free sampler', 'slug': 'free-sampler', 'access_type': 'free'}).json()['id']
        cls.premium_id = cls.client.post('/api/v1/admin/tests', headers=cls.admin, json={**base, 'title': 'Premium sampler', 'slug': 'premium-sampler', 'access_type': 'premium'}).json()['id']

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def register(self, n):
        r = self.client.post('/api/v1/auth/register', json={'first_name': f'S{n}', 'email': f'v62-{n}@example.com', 'mobile': f'90000006{n:02d}', 'password': 'StrongPass62!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    def buy_plan(self, headers):
        plan_id = self.client.get('/api/v1/plans').json()[0]['id']
        order = self.client.post('/api/v1/payments/order', headers=headers, json={'plan_id': plan_id})
        self.assertEqual(self.client.post('/api/v1/payments/development/confirm', headers=headers,
                                          json={'order_id': order.json()['order_id'], 'method': 'card'}).status_code, 200)

    def start(self, headers, tid):
        return self.client.post(f'/api/v1/tests/{tid}/start', headers=headers)

    def test_1_student_without_plan(self):
        h = self.register(1)
        blocked = self.start(h, self.premium_id)
        self.assertEqual(blocked.status_code, 403)
        self.assertIn('plan', blocked.json()['detail'])
        self.assertEqual(self.start(h, self.free_id).status_code, 200)              # free stays open
        flags = {t['id']: t['has_access'] for t in self.client.get('/api/v1/student/tests', headers=h).json()}
        self.assertEqual((flags[self.free_id], flags[self.premium_id]), (True, False))
        self.assertFalse(self.client.get(f'/api/v1/tests/{self.premium_id}', headers=h).json()['has_access'])
        self.assertTrue(self.client.get(f'/api/v1/tests/{self.free_id}', headers=h).json()['has_access'])
        # no attempt was created for the blocked test
        conn = sqlite3.connect(self.db)
        n = conn.execute('SELECT COUNT(*) FROM attempts WHERE test_id=?', (self.premium_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_2_plan_unlocks_and_expiry_relocks(self):
        h = self.register(2)
        self.assertEqual(self.start(h, self.premium_id).status_code, 403)
        self.buy_plan(h)
        started = self.start(h, self.premium_id)
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(self.client.get(f'/api/v1/tests/{self.premium_id}', headers=h).json()['has_access'])
        # plan expires: the attempt already in progress can be resumed, a new attempt cannot
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE subscriptions SET expires_at='2020-01-01T00:00:00+00:00'")
        conn.commit(); conn.close()
        self.assertEqual(self.start(h, self.premium_id).status_code, 200)
        self.client.post(f"/api/v1/attempts/{started.json()['attempt_id']}/submit", headers=h)
        again = self.start(h, self.premium_id)
        self.assertEqual(again.status_code, 403)
        self.assertFalse({t['id']: t['has_access'] for t in self.client.get('/api/v1/student/tests', headers=h).json()}[self.premium_id])

    def test_3_staff_can_preview(self):
        self.assertEqual(self.start(self.admin, self.premium_id).status_code, 200)

    def test_4_public_catalog(self):
        # no login needed
        series = self.client.get('/api/v1/public/series').json()
        s1 = next(x for x in series if x['slug'] == 'upsc-prelims-2027')
        self.assertGreaterEqual(s1['test_count'], 4)                       # 2 seeded + free + premium samplers
        self.assertGreaterEqual(s1['free_test_count'], 1)
        self.assertEqual(s1['question_count'], sum(t['total_questions'] for t in self.client.get('/api/v1/public/series/upsc-prelims-2027').json()['tests']))
        detail = self.client.get('/api/v1/public/series/upsc-prelims-2027').json()
        self.assertIn('Free sampler', [t['title'] for t in detail['tests']])
        self.assertNotIn('questions', detail['tests'][0])                   # questions are never exposed
        self.assertEqual(self.client.get('/api/v1/public/series/nope').status_code, 404)
        free = self.client.get('/api/v1/public/free-tests').json()
        self.assertEqual([t['title'] for t in free], ['Free sampler'])
        self.assertTrue(free[0]['subjects'])
        # draft series and draft tests stay hidden
        self.client.post('/api/v1/admin/series', headers=self.admin, json={'name': 'Hidden', 'slug': 'hidden', 'description': None, 'price_paise': 0, 'validity_days': 30, 'status': 'draft', 'featured': False})
        self.assertNotIn('hidden', [x['slug'] for x in self.client.get('/api/v1/public/series').json()])
        self.assertEqual(self.client.get('/api/v1/public/series/hidden').status_code, 404)


if __name__ == '__main__':
    unittest.main()
