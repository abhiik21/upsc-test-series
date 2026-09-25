# v62 — Paid-test access control and live public pages

## Access control (backend + student pages)
- **Premium / Included tests now need an active plan** to start. **Free tests stay open** to every logged-in student. Admin/staff accounts can preview any test.
- A student who already has an attempt in progress can always resume it, even if the plan expires meanwhile.
- `GET /student/tests` and `GET /tests/{id}` return `has_access`. My Tests shows a Premium/Free/Opens-at badge and an **Unlock with a plan** button; the instructions page swaps Start for **Choose a plan**; a refused start in the test window now shows a message instead of a blank page.

## Public pages now use live data
New public endpoints (no login): `GET /public/series`, `GET /public/series/{slug}`, `GET /public/free-tests`. Questions are never included.
- **Test Series**: cards come from published series (test/question counts, categories, filters). Draft series stay hidden.
- **Series detail** (`test-series-detail.html?series=<slug>`): title, description, counts and the list of published tests.
- **Free Tests**: lists tests marked *Free* in the test builder, filtered by the subjects their questions cover. Logged-out visitors are sent to register and then straight to the test.
- **Pricing**: price and validity come from the plans table; a plan hidden in the database disappears from the page.
- If the API cannot be reached, the pages keep their original static content.
- Login/registration now return to the page the student came from (same-site pages only, valid for 30 minutes).

## Known gaps (need a decision)
- Any active plan unlocks *all* premium tests; plan tiers (Starter vs Complete) are not yet different in what they unlock.
- A series' own price is not used for purchase (checkout is plan-based), so series pages point to the plans.
- PYQs page is still static.

Tests: `backend/tests_v62.py` (access rules + public catalogue); v57/v58/v60/v61/v62 pass. v58/v61 tests now buy a plan first because the seeded tests are premium.
