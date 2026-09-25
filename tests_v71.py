"""v71: a full refund cancels the plan it paid for; SMTP production checks and the test-email endpoint."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))


class V71RefundCancelsPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v71-test.db'
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
        r = self.client.post('/api/v1/auth/register', json={'first_name': f'R{n}', 'email': f'r71-{n}@example.com', 'mobile': f'90000071{n:02d}', 'password': 'StrongPass71!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': f"Bearer {r.json()['access_token']}"}

    def buy(self, h, plan_id='plan1'):
        order = self.client.post('/api/v1/payments/order', headers=h, json={'plan_id': plan_id}).json()
        pay = self.client.post('/api/v1/payments/development/confirm', headers=h, json={'order_id': order['order_id'], 'method': 'card'})
        self.assertEqual(pay.status_code, 200, pay.text)
        return pay.json()['payment']['id']

    def test_1_full_refund_cancels_the_plan(self):
        h = self.student()
        pid = self.buy(h)
        self.assertTrue(self.client.get('/api/v1/plans', headers=h).json())
        t1 = self.client.get('/api/v1/student/tests', headers=h).json()
        self.assertTrue(any(t['has_access'] and t['required_tier'] == 1 for t in t1))
        r = self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': pid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()['subscription_cancelled'])
        self.assertEqual(r.json()['status'], 'refunded')
        t2 = self.client.get('/api/v1/student/tests', headers=h).json()
        self.assertFalse(any(t['has_access'] and t['required_tier'] >= 1 and t['access_type'] != 'free' for t in t2))
        notes = self.client.get('/api/v1/student/notifications', headers=h).json()
        self.assertTrue(any('cancelled' in (n.get('title', '') + n.get('body', '')).lower() for n in notes))

    def test_2_partial_refund_keeps_the_plan(self):
        h = self.student()
        pid = self.buy(h)
        r = self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': pid, 'amount_paise': 100})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'partially_refunded')
        self.assertFalse(r.json()['subscription_cancelled'])
        self.assertTrue(any(t['has_access'] for t in self.client.get('/api/v1/student/tests', headers=h).json() if t['required_tier'] == 1))

    def test_3_refund_with_no_matching_subscription_does_not_crash(self):
        h = self.student()
        pid = self.buy(h)
        # someone already cancelled it by other means; refunding again should not error
        import sqlite3
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE subscriptions SET status='expired' WHERE payment_id=?", (pid,))
        conn.commit(); conn.close()
        r = self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': pid})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()['subscription_cancelled'])

    def test_4_second_active_plan_is_unaffected(self):
        h = self.student()
        pid1 = self.buy(h, 'plan1')
        self.buy(h, 'plan2')                                              # a second, separate plan
        self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': pid1})
        tier = max((t['required_tier'] for t in self.client.get('/api/v1/student/tests', headers=h).json() if t['has_access']), default=0)
        self.assertGreaterEqual(tier, 2)                                  # the plan2 purchase still grants access


class V71SmtpTests(unittest.TestCase):
    def test_1_production_needs_smtp_when_verification_is_required(self):
        from tests_v64 import run, GOOD_SECRET, ADMIN
        STARTUP_ONLY = 'import json\nfrom fastapi.testclient import TestClient\nimport app\nwith TestClient(app.app) as c:\n    print("RESULT " + json.dumps({"live": c.get("/health/live").status_code}))'
        db = Path(tempfile.gettempdir()) / 'upsc-v71-smtp-nosmtp.db'
        if db.exists():
            db.unlink()
        code, out, _ = run(db, {'JWT_SECRET': GOOD_SECRET, **ADMIN, 'AUTH_REQUIRE_VERIFICATION': 'true'}, code=STARTUP_ONLY)
        self.assertNotEqual(code, 0)
        self.assertIn('SMTP_HOST', out)

        db2 = Path(tempfile.gettempdir()) / 'upsc-v71-smtp-ok.db'
        if db2.exists():
            db2.unlink()
        code, out, res = run(db2, {'JWT_SECRET': GOOD_SECRET, **ADMIN, 'AUTH_REQUIRE_VERIFICATION': 'true',
                                    'SMTP_HOST': 'smtp.example.com', 'EMAIL_FROM': 'noreply@example.com'}, code=STARTUP_ONLY)
        self.assertEqual(code, 0, out)
        self.assertEqual(res['live'], 200)

    def test_2_test_email_endpoint(self):
        os.environ.update({'DEV_SQLITE_PATH': str(Path(tempfile.gettempdir()) / 'upsc-v71-testmail.db'),
                           'APP_ENV': 'development', 'DB_BACKEND': 'sqlite', 'PAYMENT_PROVIDER': 'development'})
        db = Path(os.environ['DEV_SQLITE_PATH'])
        if db.exists():
            db.unlink()
        sys.path.insert(0, str(BACKEND))
        from fastapi.testclient import TestClient
        import importlib
        import app
        importlib.reload(app)
        with TestClient(app.app) as c:
            r = c.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'})
            h = {'Authorization': f"Bearer {r.json()['access_token']}"}
            # SMTP not configured in this process
            with mock.patch.dict(os.environ, {'SMTP_HOST': '', 'EMAIL_FROM': ''}):
                resp = c.post('/api/v1/admin/notifications/test-email', headers=h, json={})
                self.assertEqual(resp.status_code, 503)
            with mock.patch.dict(os.environ, {'SMTP_HOST': 'smtp.example.com', 'EMAIL_FROM': 'noreply@example.com'}):
                with mock.patch('app.send_email') as m:
                    from services.notifications import DeliveryResult
                    m.return_value = DeliveryResult('email', True, 'smtp', 'Delivered')
                    ok = c.post('/api/v1/admin/notifications/test-email', headers=h, json={'to': 'owner@example.org'})
                    self.assertEqual(ok.status_code, 200, ok.text)
                    self.assertEqual(ok.json(), {'delivered': True, 'to': 'owner@example.org'})
                    m.return_value = DeliveryResult('email', False, 'smtp', 'Authentication failed')
                    bad = c.post('/api/v1/admin/notifications/test-email', headers=h, json={})
                    self.assertEqual(bad.status_code, 502)
                    self.assertIn('Authentication failed', bad.json()['detail'])
            db.unlink()

    def test_3_send_email_uses_real_smtplib_call(self):
        from services.notifications import send_email
        sent = {}

        class FakeSMTP:
            def __init__(self, host, port, timeout=20): sent['host'], sent['port'] = host, port
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): sent['tls'] = True
            def login(self, u, p): sent['login'] = (u, p)
            def send_message(self, m): sent['message'] = m

        with mock.patch.dict(os.environ, {'SMTP_HOST': 'smtp.example.com', 'SMTP_PORT': '587', 'SMTP_USERNAME': 'apikey', 'SMTP_PASSWORD': 'secret', 'EMAIL_FROM': 'noreply@example.com'}):
            with mock.patch('smtplib.SMTP', FakeSMTP):
                result = send_email('student@example.com', 'Hello', 'Body text')
        self.assertTrue(result.delivered)
        self.assertEqual((sent['host'], sent['port'], sent['login']), ('smtp.example.com', 587, ('apikey', 'secret')))
        self.assertEqual(sent['message']['To'], 'student@example.com')
        self.assertEqual(sent['message']['Subject'], 'Hello')

    def test_4_send_email_reports_failure_without_crashing(self):
        from services.notifications import send_email
        with mock.patch.dict(os.environ, {'SMTP_HOST': 'smtp.example.com', 'EMAIL_FROM': 'noreply@example.com'}):
            with mock.patch('smtplib.SMTP', side_effect=OSError('connection refused')):
                result = send_email('student@example.com', 'Hi', 'x')
        self.assertFalse(result.delivered)
        self.assertIn('connection refused', result.message)


if __name__ == '__main__':
    unittest.main()
