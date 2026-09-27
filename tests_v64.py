"""v64: production safety - no default admin, no demo data, no free 'development' payments, login throttle."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
GOOD_SECRET = 'x' * 48
ADMIN = {'ADMIN_EMAIL': 'owner@example.org', 'ADMIN_PASSWORD': 'a-long-passphrase-9'}

INSPECT = r'''
import json
from fastapi.testclient import TestClient
import app
out = {}
with TestClient(app.app) as c:
    def q(sql):
        conn = app.get_db(); n = conn.execute(sql).fetchone()[0]; conn.close(); return n
    out['questions'] = q('SELECT COUNT(*) FROM questions'); out['tests'] = q('SELECT COUNT(*) FROM tests')
    out['users'] = q('SELECT COUNT(*) FROM users'); out['subjects'] = q('SELECT COUNT(*) FROM subjects')
    out['plans'] = q('SELECT COUNT(*) FROM plans'); out['series'] = q('SELECT COUNT(*) FROM test_series')
    out['payments'] = q('SELECT COUNT(*) FROM payments'); out['coupons'] = q('SELECT COUNT(*) FROM coupons')
    r = c.post('/api/v1/auth/login', json={'identifier': 'owner@example.org', 'password': 'a-long-passphrase-9'})
    out['owner_login'] = r.status_code
    out['default_admin_login'] = c.post('/api/v1/auth/login', json={'identifier': 'admin@example.com', 'password': 'AdminPass1!'}).status_code
    out['public_plans'] = len(c.get('/api/v1/plans').json())
    out['docs'] = c.get('/docs').status_code
    s = c.post('/api/v1/auth/register', json={'first_name': 'S', 'email': 's@example.org', 'mobile': '9111111111', 'password': 'StrongPass1!'})
    h = {'Authorization': 'Bearer ' + s.json()['access_token']}
    out['order'] = c.post('/api/v1/payments/order', headers=h, json={'plan_id': 'plan1'}).status_code
    out['dev_confirm'] = c.post('/api/v1/payments/development/confirm', headers=h, json={'order_id': 'x', 'method': 'card'}).status_code
print('RESULT ' + json.dumps(out))
'''


def run(db, extra, code=INSPECT, app_env='production'):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('APP_ENV', 'JWT_SECRET', 'ADMIN_', 'SEED_', 'PAYMENT_', 'DB_', 'DEV_'))}
    env.update({'APP_ENV': app_env, 'DB_BACKEND': 'sqlite', 'DEV_SQLITE_PATH': str(db), 'AUTH_REQUIRE_VERIFICATION': 'false', 'PYTHONPATH': str(BACKEND)})
    env.update(extra)
    p = subprocess.run([sys.executable, '-c', code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    result = None
    for line in p.stdout.splitlines():
        if line.startswith('RESULT '):
            result = json.loads(line[7:])
    return p.returncode, (p.stderr + p.stdout), result


class V64ProductionTests(unittest.TestCase):
    def fresh(self, name):
        db = Path(tempfile.gettempdir()) / f'upsc-v64-{name}.db'
        if db.exists():
            db.unlink()
        return db

    def test_1_unsafe_settings_stop_the_server(self):
        db = self.fresh('unsafe')
        code, out, _ = run(db, {})                                               # default JWT secret
        self.assertNotEqual(code, 0); self.assertIn('JWT_SECRET', out)
        code, out, _ = run(db, {'JWT_SECRET': 'short'})
        self.assertNotEqual(code, 0); self.assertIn('JWT_SECRET', out)
        code, out, _ = run(db, {'JWT_SECRET': GOOD_SECRET})                      # no first admin given
        self.assertNotEqual(code, 0); self.assertIn('ADMIN_EMAIL', out)
        code, out, _ = run(db, {'JWT_SECRET': GOOD_SECRET, 'ADMIN_EMAIL': 'o@example.org', 'ADMIN_PASSWORD': 'short'})
        self.assertNotEqual(code, 0); self.assertIn('12 characters', out)

    def test_2_clean_production_start(self):
        db = self.fresh('clean')
        code, out, res = run(db, {'JWT_SECRET': GOOD_SECRET, **ADMIN})
        self.assertEqual(code, 0, out)
        # no demo content, but the reference data every install needs
        self.assertEqual((res['questions'], res['tests'], res['series'], res['payments'], res['coupons']), (0, 0, 0, 0, 0))
        self.assertEqual(res['users'], 1)                                        # only the owner - no demo students
        self.assertEqual((res['subjects'], res['plans'], res['public_plans']), (8, 3, 3))
        # the owner can sign in, the well-known development admin does not exist
        self.assertEqual((res['owner_login'], res['default_admin_login']), (200, 401))
        self.assertEqual(res['docs'], 404)
        # nobody can "pay" with the development provider
        self.assertEqual((res['order'], res['dev_confirm']), (503, 403))
        # restarting without the ADMIN_* variables is fine once an admin exists
        code, out, res = run(db, {'JWT_SECRET': GOOD_SECRET}, code=INSPECT.replace("'s@example.org'", "'s2@example.org'").replace('9111111111', '9111111112'))
        self.assertEqual(code, 0, out)
        self.assertEqual(res['owner_login'], 200)

    def test_3_old_database_with_default_admin_is_refused(self):
        db = self.fresh('legacy')
        code, out, _ = run(db, {}, code='import app\napp.init_db()\nprint("seeded")', app_env='development')   # what earlier versions did
        self.assertEqual(code, 0, out)
        code, out, _ = run(db, {'JWT_SECRET': GOOD_SECRET, **ADMIN})
        self.assertNotEqual(code, 0)
        self.assertIn('default password', out)

    def test_4_demo_data_only_when_asked(self):
        db = self.fresh('staging')
        code, out, res = run(db, {'JWT_SECRET': GOOD_SECRET, 'SEED_DEMO_DATA': 'true', **ADMIN})
        self.assertEqual(code, 0, out)
        self.assertGreater(res['questions'], 0)
        self.assertEqual(res['default_admin_login'], 401)                        # still no well-known admin

    def test_5_login_throttle(self):
        os.environ.update({'DEV_SQLITE_PATH': str(self.fresh('throttle')), 'APP_ENV': 'development', 'DB_BACKEND': 'sqlite', 'PAYMENT_PROVIDER': 'development'})
        sys.path.insert(0, str(BACKEND))
        from fastapi.testclient import TestClient
        import app
        with TestClient(app.app) as c:
            body = {'identifier': 'admin@example.com', 'password': 'wrong-password'}
            for _ in range(app.LOGIN_MAX_FAILURES):
                self.assertEqual(c.post('/api/v1/auth/login', json=body).status_code, 401)
            self.assertEqual(c.post('/api/v1/auth/login', json=body).status_code, 429)
            good = {'identifier': 'admin@example.com', 'password': 'AdminPass1!'}
            self.assertEqual(c.post('/api/v1/auth/login', json=good).status_code, 429)        # locked even with the right password
            other = c.post('/api/v1/auth/login', json={'identifier': 'nobody@example.com', 'password': 'x'})
            self.assertEqual(other.status_code, 401)                                            # other accounts are unaffected
            app._login_failures['admin@example.com'] = [time.time() - app.LOGIN_WINDOW_SECONDS - 5] * app.LOGIN_MAX_FAILURES
            self.assertEqual(c.post('/api/v1/auth/login', json=good).status_code, 200)         # window passed
            self.assertEqual(c.post('/api/v1/auth/login', json=body).status_code, 401)


if __name__ == '__main__':
    unittest.main()
