"""v60: bulk question import from Word (.docx) and CSV."""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def make_docx(paragraphs):
    """paragraphs: list of str or ('list', str). '\n' inside a str becomes a Word line break."""
    body = []
    for p in paragraphs:
        listed = isinstance(p, tuple)
        text = p[1] if listed else p
        runs = '<w:br/>'.join(f'<w:r><w:t xml:space="preserve">{part}</w:t></w:r>' for part in text.split('\n'))
        ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>' if listed else ''
        body.append(f'<w:p>{ppr}{runs}</w:p>')
    xml = f'<?xml version="1.0" encoding="UTF-8"?><w:document {W_NS}><w:body>{"".join(body)}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', xml)
    return buf.getvalue()


class V60QuestionImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = Path(tempfile.gettempdir()) / 'upsc-v60-test.db'
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
            'first_name': 'Stu', 'email': 'v60@example.com', 'mobile': '9000000060', 'password': 'StrongPass60!'})
        assert r.status_code == 200, r.text
        cls.student = {'Authorization': f"Bearer {r.json()['access_token']}"}

    @classmethod
    def tearDownClass(cls):
        cls.client_ctx.__exit__(None, None, None)
        try:
            cls.db.unlink()
        except FileNotFoundError:
            pass

    def upload(self, name, data, headers=None, **params):
        return self.client.post('/api/v1/admin/questions/import', headers=headers or self.admin,
                                params=params, files={'file': (name, data)})

    def test_1_real_docx_dry_run_then_import_then_duplicates(self):
        data = (BACKEND / 'sample_data' / 'QUESTION_1.docx').read_bytes()
        before = len(self.client.get('/api/v1/admin/questions', headers=self.admin).json())

        r = self.upload('QUESTION_1.docx', data, subject_id='history', dry_run='true')
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body['total'], body['valid'], body['flagged'], body['created']), (10, 10, 0, 0))
        self.assertIn('1. The total time span', body['preview'][0]['stem'])
        self.assertEqual(len(self.client.get('/api/v1/admin/questions', headers=self.admin).json()), before)

        r = self.upload('QUESTION_1.docx', data, subject_id='history', topic='Harappan Civilisation',
                        status='published', difficulty='moderate', source='Test batch', dry_run='false')
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['created'], 10)
        rows = self.client.get('/api/v1/admin/questions', headers=self.admin, params={'search': 'Harappan'}).json()
        by_stem = {x['stem']: x for x in rows}
        q1 = next(v for k, v in by_stem.items() if k.startswith('Consider the following statements regarding the Harappan Civilisation'))
        self.assertEqual((q1['correct_option'], q1['option_b'], q1['status'], q1['topic_name'], q1['subject_name']),
                         ('B', '1 and 2 only', 'published', 'Harappan Civilisation', 'History'))
        self.assertIn('Statement 3 is incorrect', q1['explanation'])
        q9 = next(v for k, v in by_stem.items() if k.startswith('The five major cities'))
        self.assertEqual(q9['correct_option'], 'B')

        again = self.upload('QUESTION_1.docx', data, subject_id='history', dry_run='false').json()
        self.assertEqual((again['valid'], again['flagged'], again['created']), (0, 10, 0))
        self.assertIn('duplicate', again['errors'][0]['problems'][0])

    def test_2_permissions_and_bad_files(self):
        data = (BACKEND / 'sample_data' / 'QUESTION_1.docx').read_bytes()
        self.assertEqual(self.upload('q.docx', data, headers=self.student, subject_id='history').status_code, 403)
        self.assertEqual(self.upload('q.docx', data, subject_id='history', status='nonsense').status_code, 400)
        self.assertEqual(self.upload('q.docx', data).status_code, 400)                       # subject required
        self.assertEqual(self.upload('q.docx', data, subject_id='nope').status_code, 400)
        self.assertEqual(self.upload('q.doc', data, subject_id='history').status_code, 400)
        self.assertEqual(self.upload('q.xlsx', b'x', subject_id='history').status_code, 400)
        self.assertEqual(self.upload('q.docx', b'not a zip', subject_id='history').status_code, 400)
        no_markers = make_docx(['Just some prose', 'without any question headings'])
        r = self.upload('q.docx', no_markers, subject_id='history')
        self.assertEqual(r.status_code, 400)
        self.assertIn('QUESTION 1', r.json()['detail'])

    def test_3_format_variations_and_flagging(self):
        docx = make_docx([
            'Q1. Which river flows through Harappa?',
            'A) Ravi B) Indus C) Ganga D) Yamuna',                   # inline options
            'Ans: (a)',
            'Question 2:',
            'Consider these:',
            ('list', 'first thing'), ('list', 'second thing'),
            'Which is correct?',
            'a. 1 only\nb. 2 only\nc. Both\nd. Neither',              # lower-case, dotted, one paragraph
            'Correct Answer: C',
            'Solution: both hold.',
            'QUESTION 3',
            'A question with no answer line',
            'A) x\nB) y\nC) z\nD) w',
            'QUESTION 4',
            'Stem with only two options',
            'A) x\nB) y',
            'Answer: A',
        ])
        r = self.upload('mix.docx', docx, subject_id='history', dry_run='true')
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body['total'], body['valid'], body['flagged']), (4, 2, 2))
        flagged = {e['number']: ' '.join(e['problems']) for e in body['errors']}
        self.assertIn("no 'Answer:' line", flagged[3])
        self.assertIn('option', flagged[4])
        previews = {p['number']: p for p in body['preview']}
        self.assertEqual(previews[1]['options']['B'], 'Indus')
        self.assertEqual(previews[1]['answer'], 'A')
        self.assertEqual(previews[2]['answer'], 'C')
        self.assertIn('1. first thing\n2. second thing', previews[2]['stem'])

    def test_4_csv_with_row_level_subjects(self):
        csv_text = (
            'Question,A,B,C,D,Answer,Subject,Topic,Difficulty,Explanation,Source,Tags\n'
            '"Which article guarantees equality?","Art 12","Art 14","Art 16","Art 19","B","Polity","Rights","Easy","Art 14","Ref","x"\n'
            '"Bad row","a","b","c","d","Z","Polity","","","","",""\n'
            '"Unknown subject row","a","b","c","d","A","Astrology","","","","",""\n'
        )
        r = self.upload('t.csv', csv_text.encode('utf-8'), dry_run='false', status='draft')
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body['total'], body['created'], body['flagged']), (3, 1, 2))
        rows = self.client.get('/api/v1/admin/questions', headers=self.admin, params={'search': 'equality'}).json()
        self.assertTrue(any(x['subject_name'] == 'Indian Polity' and x['difficulty'] == 'easy' for x in rows))


if __name__ == '__main__':
    unittest.main()
