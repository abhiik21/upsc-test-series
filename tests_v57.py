import os, sys, tempfile, unittest
from pathlib import Path

BACKEND=Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

class V57SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v57-test.db'
        try: cls.db.unlink()
        except FileNotFoundError: pass
        os.environ['DEV_SQLITE_PATH']=str(cls.db)
        os.environ['APP_ENV']='development'
        os.environ['DB_BACKEND']='sqlite'
        os.environ['DEV_SHOW_OTP']='true'
        from fastapi.testclient import TestClient
        import app
        cls.client_ctx=TestClient(app.app)
        cls.client=cls.client_ctx.__enter__()
        r=cls.client.post('/api/v1/auth/register', json={'first_name':'Test','email':'v57@example.com','mobile':'9000000011','password':'StrongPass1!'})
        assert r.status_code == 200, r.text
        cls.student_token=r.json()['access_token']

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None,None,None)
        try: cls.db.unlink()
        except FileNotFoundError: pass

    def test_otp_and_password_reset(self):
        r=self.client.post('/api/v1/auth/request-otp',json={'identifier':'v57@example.com','purpose':'email_verify'})
        self.assertEqual(r.status_code,200)
        code=r.json()['development_otp']
        self.assertEqual(self.client.post('/api/v1/auth/verify-otp',json={'identifier':'v57@example.com','purpose':'email_verify','code':code}).status_code,200)
        r=self.client.post('/api/v1/auth/request-otp',json={'identifier':'v57@example.com','purpose':'password_reset'})
        code=r.json()['development_otp']
        r=self.client.post('/api/v1/auth/verify-otp',json={'identifier':'v57@example.com','purpose':'password_reset','code':code})
        self.assertIn('reset_token', r.json())
        self.assertEqual(self.client.post('/api/v1/auth/reset-password',json={'reset_token':r.json()['reset_token'],'new_password':'NewStrongPass2!'}).status_code,200)
        self.assertEqual(self.client.post('/api/v1/auth/login',json={'identifier':'v57@example.com','password':'NewStrongPass2!'}).status_code,200)

    def test_file_upload_access_control(self):
        admin=self.client.post('/api/v1/auth/login',json={'identifier':'admin@example.com','password':'AdminPass1!'}).json()['access_token']
        student=self.client.post('/api/v1/auth/login',json={'identifier':'v57@example.com','password':'StrongPass1!'}).json()['access_token']
        ah={'Authorization':f'Bearer {admin}'}; sh={'Authorization':f'Bearer {student}'}
        r=self.client.post('/api/v1/admin/files/upload?access_type=premium',headers=ah,files={'file':('premium.txt',b'premium','text/plain')})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/v1/files/'+r.json()['id'],headers=sh).status_code,403)
        r=self.client.post('/api/v1/admin/files/upload?access_type=free',headers=ah,files={'file':('free.txt',b'free','text/plain')})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/v1/files/'+r.json()['id'],headers=sh).status_code,200)

    def test_admin_broadcast_notification(self):
        admin=self.client.post('/api/v1/auth/login',json={'identifier':'admin@example.com','password':'AdminPass1!'}).json()['access_token']
        r=self.client.post('/api/v1/admin/notifications',headers={'Authorization':f'Bearer {admin}'},json={'title':'System update','body':'Test notification','type':'system','channel':'in_app'})
        self.assertEqual(r.status_code,200)
        self.assertGreaterEqual(r.json()['created'],1)

if __name__=='__main__': unittest.main()
