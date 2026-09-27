"""v70: real plan tiers - each plan unlocks a different, hierarchical set of tests."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V70PlanTierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v70-test.db'
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
        cls.plans = {p['id']: p for p in cls.client.get('/api/v1/plans').json()}
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def student(self):
        type(self).n += 1; n = type(self).n
        r = self.client.post('/api/v1/auth/register', json={'first_name': f'T{n}', 'email': f't70-{n}@example.com', 'mobile': f'90000070{n:02d}', 'password': 'StrongPass70!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    def buy(self, h, plan_id):
        order = self.client.post('/api/v1/payments/order', headers=h, json={'plan_id': plan_id}).json()
        self.assertEqual(self.client.post('/api/v1/payments/development/confirm', headers=h, json={'order_id': order['order_id'], 'method': 'card'}).status_code, 200)

    def make_test(self, title, tier, access='premium'):
        qids = [q['id'] for q in self.client.get('/api/v1/admin/questions', headers=self.admin, params={'limit': 2}).json()]
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json={
            'series_id': 'series1', 'title': title, 'slug': title.lower().replace(' ', '-'), 'description': None, 'test_type': 'subject',
            'duration_minutes': 10, 'total_marks': 4, 'negative_mark': 0.67, 'access_type': access, 'required_tier': tier,
            'scheduled_at': None, 'status': 'published', 'question_ids': qids})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_1_plans_have_the_expected_tiers(self):
        self.assertEqual([self.plans[p]['tier'] for p in ('plan1', 'plan2', 'plan3')], [1, 2, 3])

    def test_2_tier_hierarchy(self):
        t1 = self.make_test('Tier1 test', 1)
        t2 = self.make_test('Tier2 test', 2)
        t3 = self.make_test('Tier3 test', 3)

        starter = self.student(); self.buy(starter, 'plan1')
        self.assertEqual(self.client.post(f"/api/v1/tests/{t1['id']}/start", headers=starter).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t2['id']}/start", headers=starter).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t3['id']}/start", headers=starter).status_code, 403)
        flags = {t['title']: t['has_access'] for t in self.client.get('/api/v1/student/tests', headers=starter).json()}
        self.assertEqual((flags['Tier1 test'], flags['Tier2 test'], flags['Tier3 test']), (True, False, False))

        complete = self.student(); self.buy(complete, 'plan2')
        self.assertEqual(self.client.post(f"/api/v1/tests/{t1['id']}/start", headers=complete).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t2['id']}/start", headers=complete).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t3['id']}/start", headers=complete).status_code, 403)

        top = self.student(); self.buy(top, 'plan3')
        for t in (t1, t2, t3):
            self.assertEqual(self.client.post(f"/api/v1/tests/{t['id']}/start", headers=top).status_code, 200, t['title'])

    def test_3_free_tests_ignore_tier(self):
        free = self.make_test('Free regardless', 3, access='free')
        h = self.student()
        self.assertEqual(self.client.post(f"/api/v1/tests/{free['id']}/start", headers=h).status_code, 200)

    def test_4_upgrade_and_multiple_plans(self):
        h = self.student()
        self.buy(h, 'plan1')
        t2 = self.make_test('Upgrade target', 2)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t2['id']}/start", headers=h).status_code, 403)
        self.buy(h, 'plan2')                                                    # buys a second, higher plan on top
        self.assertEqual(self.client.post(f"/api/v1/tests/{t2['id']}/start", headers=h).status_code, 200)

    def test_5_validation(self):
        qids = [q['id'] for q in self.client.get('/api/v1/admin/questions', headers=self.admin, params={'limit': 1}).json()]
        body = {'series_id': 'series1', 'title': 'Bad tier', 'slug': 'bad-tier', 'description': None, 'test_type': 'subject',
                'duration_minutes': 10, 'total_marks': 2, 'negative_mark': 0.67, 'access_type': 'premium', 'required_tier': 7,
                'scheduled_at': None, 'status': 'draft', 'question_ids': qids}
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json=body)
        self.assertEqual(r.status_code, 400)
        self.assertIn('required_tier', r.json()['detail'])
        body['required_tier'] = 0
        self.assertEqual(self.client.post('/api/v1/admin/tests', headers=self.admin, json=body).status_code, 422)
        # a free test is not blocked by an out-of-range tier value (it is stored as 1 regardless)
        body['access_type'] = 'free'; body['required_tier'] = 7
        r = self.client.post('/api/v1/admin/tests', headers=self.admin, json=body)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['required_tier'], 1)

    def test_6_staff_preview_ignores_tier(self):
        t3 = self.make_test('Staff preview tier3', 3)
        self.assertEqual(self.client.post(f"/api/v1/tests/{t3['id']}/start", headers=self.admin).status_code, 200)

    def test_7_public_series_shows_required_tier(self):
        self.make_test('Public tier check', 2)
        detail = self.client.get('/api/v1/public/series/upsc-prelims-2027').json()
        row = next(t for t in detail['tests'] if t['title'] == 'Public tier check')
        self.assertEqual(row['required_tier'], 2)


if __name__ == '__main__':
    unittest.main()
