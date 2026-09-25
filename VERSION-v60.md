# v60 — Bulk question import (Word .docx and CSV)

- Fixed: **Question Bank → Bulk Import** was a placeholder. It only showed a fixed "973 rows ready" message, accepted no Word files and never saved anything.
- New `POST /api/v1/admin/questions/import` (admin only): parses a Word (.docx) or CSV file, validates it (dry run) and then creates the questions.
  - Word format: a `QUESTION n` heading per question, the question (paragraphs, bullet statements, tables), options `A)`–`D)`, `Answer: B`, optional `Explanation:`.
  - Bullet statements are numbered 1, 2, 3 … and tables become `a | b` rows so the statements in the question can be answered by number.
  - Flags and skips: missing/invalid answer, missing options, duplicates (in the bank or in the same file). Nothing is written during validation.
  - The chosen Subject / Topic (created if new) / Difficulty / Status / Source apply to every question in a Word file; CSV rows may carry their own.
- Admin page: real Validate → preview → Import flow, then the question list refreshes.
- Student pages: question text and explanations keep their line breaks (statements were previously run together).
- `api-client.js`: `UPSC_API.upload()` for multipart uploads; readable messages for validation errors.
- Tests: `backend/tests_v60.py` (uses `backend/sample_data/QUESTION_1.docx`). v57 / v58 / v60 suites pass.
