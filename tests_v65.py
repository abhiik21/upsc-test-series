"""v65: Razorpay checkout - orders, browser verification, webhook, refunds, production settings.
Razorpay itself is replaced by a stand-in, so nothing here talks to the internet."""
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

KEY_ID, KEY_SECRET, WEBHOOK_SECRET = 'rzp_test_abc123', 'test_secret_xyz', 'whsec_test_456'
calls = []
fail = {'order': False, 'refund': False}


def fake_request(self, method, path, payload=None):
    from payment.razorpay_provider import PaymentGatewayError
    calls.append((method, path, payload))
    if path == '/orders':
        if fail['order']:
            raise PaymentGatewayError('Razorpay rejected the request (400): bad')
        n = len([c for c in calls if c[1] == '/orders'])
        return {'id': f'order_T{n}', 'amount': payload['amount'], 'currency': 'INR', 'status': 'created'}
    if path.endswith('/refund'):
        if fail['refund']:
            raise PaymentGatewayError('Razorpay rejected the request (400): refund not allowed')
        return {'id': 'rfnd_1'}
    raise AssertionError(f'unexpected gateway call {method} {path}')


def sign(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest()


class V65RazorpayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v65-test.db'
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass
        os.environ.update({'DEV_SQLITE_PATH': str(cls.db), 'APP_ENV': 'development', 'DB_BACKEND': 'sqlite', 'DEV_SHOW_OTP': 'true',
                           'PAYMENT_PROVIDER': 'razorpay', 'RAZORPAY_KEY_ID': KEY_ID, 'RAZORPAY_KEY_SECRET': KEY_SECRET,
                           'RAZORPAY_WEBHOOK_SECRET': WEBHOOK_SECRET})
        from fastapi.testclient import TestClient
        import app
        from payment.razorpay_provider import RazorpayProvider
        cls.app = app
        cls.patch = mock.patch.object(RazorpayProvider, '_request', fake_request)
        cls.patch.start()
        cls.client_ctx = TestClient(app.app)
        cls.client = cls.client_ctx.__enter__()
        r = cls.client.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'})
        cls.admin = {'Authorization': f"Bearer {r.json()['access_token']}"}
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    # ---- helpers
    def student(self):
        type(self).n += 1
        n = type(self).n
        r = self.client.post('/api/v1/auth/register', json={'first_name': f'Pay{n}', 'email': f'pay{n}@example.com', 'mobile': f'90000065{n:02d}', 'password': 'StrongPass65!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': f"Bearer {r.json()['access_token']}"}, f'pay{n}@example.com'

    def sql(self, query, *args):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(query, args).fetchall()
        finally:
            conn.commit(); conn.close()

    def subs(self, email):
        return self.sql("SELECT COUNT(*) FROM subscriptions s JOIN users u ON u.id=s.user_id WHERE u.email=? AND s.status='active'", email)[0][0]

    def order(self, h, plan='plan1', coupon=None):
        return self.client.post('/api/v1/payments/order', headers=h, json={'plan_id': plan, 'coupon_code': coupon})

    def webhook(self, event, payment, secret=WEBHOOK_SECRET, sig=None):
        body = json.dumps({'event': event, 'payload': {'payment': {'entity': payment}}}).encode()
        signature = sig if sig is not None else hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post('/api/v1/payments/razorpay/webhook', content=body, headers={'X-Razorpay-Signature': signature, 'Content-Type': 'application/json'})

    # ---- tests
    def test_1_signature_checks(self):
        from payment.razorpay_provider import RazorpayConfig, RazorpayProvider
        rz = RazorpayProvider(RazorpayConfig(KEY_ID, KEY_SECRET, WEBHOOK_SECRET))
        good = sign('order_A', 'pay_A')
        self.assertTrue(rz.verify_payment_signature('order_A', 'pay_A', good))
        self.assertFalse(rz.verify_payment_signature('order_A', 'pay_B', good))            # different payment
        self.assertFalse(rz.verify_payment_signature('order_B', 'pay_A', good))            # different order
        self.assertFalse(rz.verify_payment_signature('order_A', 'pay_A', sign('order_A', 'pay_A', 'other-secret')))
        self.assertFalse(rz.verify_payment_signature('order_A', 'pay_A', ''))
        body = b'{"event":"payment.captured"}'
        self.assertTrue(rz.verify_webhook(body, hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()))
        self.assertFalse(rz.verify_webhook(body + b' ', hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()))

    def test_2_order_then_browser_verification(self):
        h, email = self.student()
        before = self.client.get('/api/v1/student/tests', headers=h).json()
        self.assertFalse(any(t['has_access'] for t in before if t['access_type'] == 'premium'))
        r = self.order(h)
        self.assertEqual(r.status_code, 200, r.text)
        o = r.json()
        price = self.client.get('/api/v1/plans').json()[0]['price_paise']
        self.assertEqual((o['provider'], o['key_id'], o['amount_paise'], o['prefill']['email']), ('razorpay', KEY_ID, price, email))
        self.assertTrue(o['order_id'].startswith('order_T'))
        method, path, payload = [c for c in calls if c[1] == '/orders'][-1]
        self.assertEqual((payload['amount'], payload['currency']), (price, 'INR'))
        self.assertTrue(payload['receipt'].startswith('rcpt_') and len(payload['receipt']) <= 40)
        self.assertEqual(self.subs(email), 0)                                              # nothing is active before payment
        self.assertEqual(self.client.post('/api/v1/payments/development/confirm', headers=h, json={'order_id': o['order_id'], 'method': 'upi'}).status_code, 403)

        pid = 'pay_' + o['order_id'][-3:] + 'X1'
        forged = self.client.post('/api/v1/payments/razorpay/verify', headers=h, json={'razorpay_order_id': o['order_id'], 'razorpay_payment_id': pid, 'razorpay_signature': 'deadbeef'})
        self.assertEqual(forged.status_code, 400)
        self.assertEqual(self.subs(email), 0)
        ok = self.client.post('/api/v1/payments/razorpay/verify', headers=h, json={'razorpay_order_id': o['order_id'], 'razorpay_payment_id': pid, 'razorpay_signature': sign(o['order_id'], pid)})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual((ok.json()['status'], ok.json()['payment']['gateway'], ok.json()['payment']['transaction_id']), ('paid', 'razorpay', pid))
        self.assertEqual(self.subs(email), 1)
        again = self.client.post('/api/v1/payments/razorpay/verify', headers=h, json={'razorpay_order_id': o['order_id'], 'razorpay_payment_id': pid, 'razorpay_signature': sign(o['order_id'], pid)})
        self.assertEqual((again.status_code, again.json()['status']), (200, 'paid'))
        self.assertEqual(self.subs(email), 1)                                              # a repeat does not add a second plan
        self.assertEqual(self.sql("SELECT COUNT(*) FROM payments WHERE order_id=?", o['order_id'])[0][0], 1)
        after = self.client.get('/api/v1/student/tests', headers=h).json()
        self.assertTrue(all(t['has_access'] for t in after if t['required_tier'] <= 1))    # tier-1 tests are now unlocked

        # webhook arrives afterwards: no duplicate, but the payment method becomes known
        w = self.webhook('payment.captured', {'id': pid, 'order_id': o['order_id'], 'amount': price, 'currency': 'INR', 'method': 'upi'})
        self.assertEqual(w.status_code, 200)
        self.assertEqual(self.subs(email), 1)
        self.assertEqual(self.sql("SELECT method FROM payments WHERE order_id=?", o['order_id'])[0][0], 'upi')

    def test_3_someone_elses_or_unknown_orders(self):
        a, _ = self.student(); b, email_b = self.student()
        o = self.order(a).json()
        r = self.client.post('/api/v1/payments/razorpay/verify', headers=b, json={'razorpay_order_id': o['order_id'], 'razorpay_payment_id': 'pay_Z', 'razorpay_signature': sign(o['order_id'], 'pay_Z')})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.subs(email_b), 0)
        r = self.client.post('/api/v1/payments/razorpay/verify', headers=b, json={'razorpay_order_id': 'order_nope', 'razorpay_payment_id': 'pay_Z', 'razorpay_signature': sign('order_nope', 'pay_Z')})
        self.assertEqual(r.status_code, 404)

    def test_4_webhook_alone_activates_the_plan(self):
        h, email = self.student()
        o = self.order(h).json()
        pay = {'id': 'pay_WH1', 'order_id': o['order_id'], 'amount': o['amount_paise'], 'currency': 'INR', 'method': 'card'}
        self.assertEqual(self.webhook('payment.captured', pay, sig='bad').status_code, 400)
        self.assertEqual(self.webhook('payment.captured', pay, secret='wrong-secret').status_code, 400)
        self.assertEqual(self.subs(email), 0)
        self.assertEqual(self.webhook('payment.captured', {**pay, 'amount': 100}).json()['status'], 'ignored')     # wrong amount
        self.assertEqual(self.subs(email), 0)
        self.assertEqual(self.webhook('payment.captured', {**pay, 'order_id': 'order_unknown'}).json()['status'], 'ignored')
        self.assertEqual(self.webhook('payment.captured', pay).json()['status'], 'paid')
        self.assertEqual(self.subs(email), 1)
        self.assertEqual(self.webhook('payment.captured', pay).json()['status'], 'paid')                        # Razorpay retries: harmless
        self.assertEqual(self.webhook('order.paid', pay).json()['status'], 'paid')
        self.assertEqual(self.subs(email), 1)
        self.assertEqual(self.sql("SELECT COUNT(*),method FROM payments WHERE order_id=?", o['order_id'])[0], (1, 'card'))
        self.assertEqual(self.webhook('refund.created', pay).json()['status'], 'ignored')                       # events we do not use

    def test_5_failed_payment_is_recorded_not_activated(self):
        h, email = self.student()
        o = self.order(h).json()
        r = self.webhook('payment.failed', {'id': 'pay_FAIL1', 'order_id': o['order_id'], 'amount': o['amount_paise'], 'currency': 'INR', 'method': 'upi'})
        self.assertEqual((r.status_code, r.json()['status']), (200, 'recorded'))
        self.assertEqual(self.subs(email), 0)
        self.assertEqual(self.sql("SELECT status FROM payments WHERE transaction_id='pay_FAIL1'")[0][0], 'failed')
        self.assertEqual(self.sql("SELECT status FROM checkout_orders WHERE provider_order_id=?", o['order_id'])[0][0], 'created')   # can still be paid

    def test_6_webhook_needs_its_secret(self):
        with mock.patch.dict(os.environ, {'RAZORPAY_WEBHOOK_SECRET': ''}):
            self.assertEqual(self.webhook('payment.captured', {'id': 'pay_X', 'order_id': 'o', 'amount': 1}, secret='').status_code, 503)

    def test_7_free_and_tiny_totals(self):
        h, email = self.student()
        self.sql("INSERT INTO coupons VALUES ('cpfree','FREE100','Test','percentage',100,'all','2020-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00',10,1,0,'active')")
        n = len(calls)
        r = self.order(h, coupon='FREE100')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()['provider'], r.json()['status'], r.json()['amount_paise']), ('free', 'paid', 0))
        self.assertEqual(len(calls), n)                                                    # nothing was sent to Razorpay
        self.assertEqual(self.subs(email), 1)
        h2, _ = self.student()
        self.sql("INSERT INTO coupons VALUES ('cptiny','TINY','Test','flat',498.5,'all','2020-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00',10,1,0,'active')")
        r = self.order(h2, coupon='TINY')                                                  # 50 paise is below Razorpay's minimum
        self.assertEqual(r.status_code, 400)

    def test_8_gateway_down(self):
        h, email = self.student()
        before = self.sql("SELECT COUNT(*) FROM checkout_orders")[0][0]
        fail['order'] = True
        try:
            r = self.order(h)
        finally:
            fail['order'] = False
        self.assertEqual(r.status_code, 502)
        self.assertNotIn('Razorpay rejected', r.text)                                      # no gateway internals shown to students
        self.assertEqual(self.sql("SELECT COUNT(*) FROM checkout_orders")[0][0], before)

    def test_9_refund_goes_to_razorpay(self):
        h, email = self.student()
        o = self.order(h).json()
        pid = 'pay_REF1'
        paid = self.client.post('/api/v1/payments/razorpay/verify', headers=h, json={'razorpay_order_id': o['order_id'], 'razorpay_payment_id': pid, 'razorpay_signature': sign(o['order_id'], pid)}).json()
        payment_id = paid['payment']['id']
        fail['refund'] = True
        try:
            r = self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': payment_id, 'amount_paise': 10000})
        finally:
            fail['refund'] = False
        self.assertEqual(r.status_code, 502)
        self.assertEqual(self.sql("SELECT status FROM payments WHERE id=?", payment_id)[0][0], 'successful')   # unchanged when Razorpay says no
        r = self.client.post('/api/v1/admin/payments/refund', headers=self.admin, json={'payment_id': payment_id, 'amount_paise': 10000})
        self.assertEqual(r.status_code, 200, r.text)
        method, path, payload = calls[-1]
        self.assertEqual((method, path, payload), ('POST', '/payments/pay_REF1/refund', {'amount': 10000}))
        self.assertEqual(self.sql("SELECT status,refund_amount_paise FROM payments WHERE id=?", payment_id)[0], ('partially_refunded', 10000))


class V65ProductionSettingsTests(unittest.TestCase):
    CODE = 'import json\nfrom fastapi.testclient import TestClient\nimport app\nwith TestClient(app.app) as c:\n    print("RESULT " + json.dumps({"live": c.get("/health/live").status_code}))'

    def run_prod(self, name, extra):
        from tests_v64 import run, GOOD_SECRET, ADMIN
        db = Path(tempfile.gettempdir()) / f'upsc-v65-{name}.db'
        if db.exists():
            db.unlink()
        return run(db, {'JWT_SECRET': GOOD_SECRET, **ADMIN, **extra}, code=self.CODE)

    def test_razorpay_settings_are_checked(self):
        code, out, _ = self.run_prod('nokeys', {'PAYMENT_PROVIDER': 'razorpay'})
        self.assertNotEqual(code, 0); self.assertIn('RAZORPAY_KEY_ID', out)
        code, out, _ = self.run_prod('nowh', {'PAYMENT_PROVIDER': 'razorpay', 'RAZORPAY_KEY_ID': 'rzp_live_x', 'RAZORPAY_KEY_SECRET': 's'})
        self.assertNotEqual(code, 0); self.assertIn('RAZORPAY_WEBHOOK_SECRET', out)
        code, out, _ = self.run_prod('stripe', {'PAYMENT_PROVIDER': 'stripe'})
        self.assertNotEqual(code, 0); self.assertIn('not supported', out)
        code, out, res = self.run_prod('ok', {'PAYMENT_PROVIDER': 'razorpay', 'RAZORPAY_KEY_ID': 'rzp_live_x', 'RAZORPAY_KEY_SECRET': 's', 'RAZORPAY_WEBHOOK_SECRET': 'w'})
        self.assertEqual(code, 0, out)
        self.assertEqual(res['live'], 200)


if __name__ == '__main__':
    unittest.main()
