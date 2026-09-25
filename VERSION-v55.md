# UPSC Test Series — v55

## Payment & Checkout Foundation

- Added authenticated checkout page.
- Added server-side payment quote calculation.
- Added coupon validation and per-user/redemption-limit checks.
- Added checkout order creation.
- Added development payment confirmation flow for local QA.
- Successful development payment activates the selected subscription.
- Added student payment history and invoice lookup API.
- Updated Pricing and Test Series Detail purchase CTAs to launch checkout.
- Added payment-success notification.
- Added payment-provider guard so development confirmation cannot be used when a production provider is configured.

## Verification

Smoke-tested: Register → Login → Quote → Coupon → Order → Development Payment Confirmation → Subscription Activation → Payment History.

## Production note

No live gateway credentials or real payment transactions are enabled. A concrete gateway adapter (e.g. Razorpay/Cashfree) must be configured and independently tested before production payment collection is enabled.
