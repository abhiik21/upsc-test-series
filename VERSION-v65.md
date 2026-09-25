# v65 — Razorpay payments

- **Checkout works with Razorpay** (`PAYMENT_PROVIDER=razorpay`): the server creates the order (amount always calculated on the server, coupons applied), the checkout page opens Razorpay's payment window, and the plan is activated only after the payment is confirmed.
- **Two independent confirmations, both idempotent**: the browser calls `POST /payments/razorpay/verify` (HMAC-SHA256 signature check with your key secret), and Razorpay calls `POST /payments/razorpay/webhook` (signature check with the webhook secret). Whichever arrives first activates the plan; the other changes nothing (no double plans, no double payment rows). The webhook also covers students who close the browser after paying.
- Webhook checks the paid amount against the order and records `payment.failed` without activating anything.
- A coupon that brings the total to ₹0 activates the plan directly; a total under ₹1 is refused (Razorpay's minimum).
- **Refunds** made in Admin → Payments are now sent to Razorpay first; if Razorpay refuses, nothing is changed in the database.
- Production start-up requires `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET` when `PAYMENT_PROVIDER=razorpay`, and warns while test keys are in use. Unknown providers are refused.
- Login returns to the page that asked for it (`login.html?next=checkout.html?...`), so buying a plan continues after signing in.
- Development mode (`PAYMENT_PROVIDER=development`) still works for local testing.
- `deployment/README.md` has the step-by-step Razorpay setup (test keys, webhook, going live).

## Verified
Razorpay's servers are replaced by a stand-in in the tests, and the checkout page was driven end to end (success, closed window, failed payment, forged confirmation, 100% coupon). **It has not been run against Razorpay itself** — do a full test-mode purchase (deployment/README.md, section 5) before using live keys.

## Not covered
Tax invoices (invoice numbers are internal receipts); cancelling a plan automatically when a payment is refunded.

Tests: `backend/tests_v65.py`; v57/v58/v60–v65 pass.
