# v61 — Working test builder (questions -> tests)

The admin **Test Management** page is now live instead of a demo:

- **Create / edit tests** from the questions in the bank. Every field is saved: title, series, type, duration, access, release date/time, description, marks per correct answer, negative marking.
- **Question picker**: live question bank with Subject / Difficulty / Status filters and text search, select-all-shown, click-a-row to toggle. Selected questions can be reordered or removed; the order saved is the order students see.
- **Duplicate** a test as a draft copy. **Save as Draft** / **Save & Publish**.
- **Release date is enforced**: students get "This test opens on ..." before that time.
- **Safety rules**: a test cannot be published without questions; once students have attempted a test its questions and marking are locked (title, access, release and status can still change; duplicate it to make a new version); duplicate titles get a unique web address automatically.
- Stats cards, series overview, upcoming releases and the test table use real data. **+ New Test Series** now really creates a series.
- Removed the placeholder switches that did nothing (randomise order, distribution rules, student-facing settings) rather than pretending to save them.

Backend: `GET /admin/tests/{id}`, filters `difficulty` and `limit` on `GET /admin/questions`, validation on create/update, duplicate series names handled.
Bulk import now keeps the Word file's question order in lists.
Tests: `backend/tests_v61.py`; v57/v58/v60/v61 pass.
