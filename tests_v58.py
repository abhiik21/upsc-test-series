import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V58RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v58-test.db'
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass
        os.environ.update({
            'DEV_SQLITE_PATH': str(cls.db),
            'APP_ENV': 'development',
            'DB_BACKEND': 'sqlite',
            'DEV_SHOW_OTP': 'true',
            'PAYMENT_PROVIDER': 'development',
        })
        from fastapi.testclient import TestClient
        import app
        cls.client_ctx = TestClient(app.app)
        cls.client = cls.client_ctx.__enter__()
        r = cls.client.get('/health/live')
        assert r.status_code == 200
        r = cls.client.get('/health/ready')
        assert r.status_code == 200
        r = cls.client.post('/api/v1/auth/register', json={
            'first_name': 'Regression',
            'email': 'v58@example.com',
            'mobile': '9000000058',
            'password': 'StrongPass58!',
        })
        assert r.status_code == 200, r.text
        cls.token = r.json()['access_token']
        cls.headers = {'Authorization': f'Bearer {cls.token}'}

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def test_authenticated_student_flow(self):
        self.assertEqual(self.client.get('/api/v1/auth/me', headers=self.headers).status_code, 200)
        tests = self.client.get('/api/v1/student/tests', headers=self.headers)
        self.assertEqual(tests.status_code, 200)
        test_id = tests.json()[0]['id']
        # the seeded tests are premium: buy a plan first (development payment flow)
        plan_id = self.client.get('/api/v1/plans').json()[0]['id']
        order = self.client.post('/api/v1/payments/order', headers=self.headers, json={'plan_id': plan_id})
        self.client.post('/api/v1/payments/development/confirm', headers=self.headers, json={'order_id': order.json()['order_id'], 'method': 'card'})
        started = self.client.post(f'/api/v1/tests/{test_id}/start', headers=self.headers)
        self.assertEqual(started.status_code, 200)
        attempt_id = started.json()['attempt_id']
        q = started.json()['questions'][0]
        saved = self.client.post(f"/api/v1/attempts/{attempt_id}/questions/{q['id']}/answer", headers=self.headers, json={
            'selected_option': 'A', 'time_spent_seconds': 4
        })
        self.assertEqual(saved.status_code, 200)
        submitted = self.client.post(f'/api/v1/attempts/{attempt_id}/submit', headers=self.headers)
        self.assertEqual(submitted.status_code, 200)
        result = self.client.get(f'/api/v1/tests/{test_id}/result', headers=self.headers)
        self.assertEqual(result.status_code, 200)

    def test_payment_dev_flow(self):
        plans = self.client.get('/api/v1/plans')
        self.assertEqual(plans.status_code, 200)
        plan_id = plans.json()[0]['id']
        order = self.client.post('/api/v1/payments/order', headers=self.headers, json={'plan_id': plan_id})
        self.assertEqual(order.status_code, 200)
        confirmed = self.client.post('/api/v1/payments/development/confirm', headers=self.headers, json={
            'order_id': order.json()['order_id'], 'method': 'card'
        })
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()['status'], 'paid')

    def test_security_headers_and_request_id(self):
        r = self.client.get('/health/live', headers={'X-Request-ID': 'regression-v58'})
        self.assertEqual(r.headers.get('X-Request-ID'), 'regression-v58')
        self.assertEqual(r.headers.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.headers.get('X-Frame-Options'), 'DENY')


if __name__ == '__main__':
    unittest.main()
