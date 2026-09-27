# v63 — Previous-year questions (PYQs) go live

- **Bulk import can tag the exam year**: new "UPSC exam year" field in the import window (applies to every question in the file); CSV files may also have a `Year` column. Untagged questions stay ordinary practice questions.
- **Public PYQs page uses real data**: `GET /api/v1/public/pyqs` (no login) returns published questions that carry an exam year, with options, answer key and explanation. Filters: year, subject, search (server side), paper and topic (in the page), sort newest/oldest. The demo "sample record" cards are removed.
- The student PYQ page already filtered by year, so tagged questions appear there too.
- Draft questions and questions without a year never appear publicly.

Note: a fresh install ships three sample questions (Article 14, monetary policy, species diversity) that carry the years 2024/2023/2022, so they show up as PYQs until you unpublish or delete them. The two sample tests use them as well.

Tests: `backend/tests_v63.py`; v57/v58/v60/v61/v62/v63 pass.
