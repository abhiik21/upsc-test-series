# v70/v71 — Real plan tiers, refund cancels the plan, SMTP checks

## Real plan tiers (fixes the plan-vs-content mismatch)
- Plans are now hierarchical: Starter = tier 1, Complete = tier 2, Complete + CA = tier 3. A higher-tier plan unlocks everything a lower one does.
- Each test now has a **"Minimum plan required"** field in the test builder (hidden for Free tests). A student needs an active plan at that tier or higher to start it.
- `/student/tests` and `/tests/{id}` report real per-test access based on the student's highest active plan.
- Public Test Series page: each test in a series is labelled with the plan that unlocks it; plan card bullets now describe the real hierarchy instead of fabricated test counts, and the two starter bullets are filled with live cumulative test/question counts per tier (`/public/stats` -> `by_tier`).
- The two sample tests now sit at different tiers (tier 1 and tier 2) so the hierarchy is visible out of the box.
- Buying a second, higher plan while an existing one is active adds to (does not replace) what the student can access.

## Refund cancels the plan (fixes "no plan-cancellation-on-refund")
- A **full** refund now cancels the specific plan that payment purchased; the student loses access immediately and gets a notification. A **partial** refund leaves the plan active.
- If the student holds a separate, second plan, it is unaffected.
- The Admin → Payments page was rebuilt: it was showing entirely fabricated numbers and names, and its Refund button was a demo that did not call the API at all. It now shows real transactions and stats, and Refund actually processes a refund (and, for Razorpay, sends it to Razorpay first).

## SMTP checks
- Production now refuses to start if `AUTH_REQUIRE_VERIFICATION=true` but SMTP isn't configured, so this is caught at deploy time rather than when the first student tries to register.
- New **Send Test Email** button (Admin → Settings) sends one real email through your SMTP settings, to confirm they work before relying on them.
- The email-sending code itself was already fully built; setting up an actual SMTP account (Gmail app password, SendGrid, Brevo, etc.) and entering its details in `deployment/.env` is still something only you can do.

Tests: `backend/tests_v70.py` (tier hierarchy), `backend/tests_v71.py` (refund cancellation, SMTP checks, real send_email call). All suites pass.
