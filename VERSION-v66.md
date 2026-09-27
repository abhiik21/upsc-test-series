# v66 — Remove invented content from public pages

A content audit found public pages showing figures that did not come from real data.

- **Results page** (linked from every page as "Results / Toppers"): showed a made-up leaderboard - "4,823 participants", "Aspirant A-F", dated tests. It now shows real, anonymous, aggregate results per test (participants, average, highest score). Names and individual scores are never published; a test appears only after at least 5 students have completed it (`PUBLIC_RESULTS_MIN_PARTICIPANTS`). With no data it says so. The menu item is now "Results".
- **Home page** "1000+ Practice Questions / 25+ Planned Tests" and **Test Series page** "25+ / 2,500+" now show live counts of published questions and tests (`GET /public/stats`).
- **Series page**: removed the invented "₹1,499 - Limited launch price" strike-through.
- New endpoints: `GET /public/results`, `GET /public/stats` (no login, no personal data).

## Still to review by you (not changed)
- **Pricing page / student subscription page**: plan bullets such as "10 curated tests", "25 tests", "2,500+ questions" are marketing text; they do not match what any plan really unlocks.
- **Admin → Results** (`results-admin.html`) still shows sample figures (it is not connected to data). Spot-check the other admin report pages too.
- **Legal pages** (Privacy, Terms, Refund, Disclaimer, Cookies) are short generic text with no business name, address or contact details, and must match your real refund policy. Payment providers usually check for these pages.
- The blog article "Mock test analysis", About and FAQ are static text: read them for claims you cannot stand behind.

Tests: `backend/tests_v66.py`; v57/v58/v60-v66 pass.
