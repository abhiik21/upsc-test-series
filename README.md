# UPSC Test Series Backend

FastAPI backend for the UPSC Test Series platform.

## Local development

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements.txt
set DB_BACKEND=sqlite       # Windows cmd
# export DB_BACKEND=sqlite  # macOS/Linux
uvicorn app:app --reload --port 8000
```

API docs: `http://127.0.0.1:8000/docs`

## Authentication

- JWT access tokens
- OTP verification endpoints
- Password-reset OTP + one-time reset tokens
- Optional mandatory email verification via `AUTH_REQUIRE_VERIFICATION=true`
- Development OTP is shown only when `DEV_SHOW_OTP=true` outside production

## Notifications

Email delivery uses SMTP when `SMTP_HOST` and `EMAIL_FROM` are configured. In-app notifications are stored in the database. Delivery attempts are recorded in `notification_deliveries`.

## File storage

The admin file-upload endpoint supports `local` and S3-compatible object storage. Production should use S3/object storage and private access for protected resources.

## Database

SQLite is convenient for local development. PostgreSQL is the intended production backend; set `DB_BACKEND=postgres` and `DATABASE_URL`.

## Payments

Razorpay is implemented in `payment/razorpay_provider.py` (Orders API, Checkout signature check, webhook signature check, refunds; standard library only). Set `PAYMENT_PROVIDER=razorpay` with `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET`; the browser confirms via `POST /payments/razorpay/verify` and Razorpay itself calls `POST /payments/razorpay/webhook`. In production the development provider is switched off (checkout answers 503). Keep credentials in environment variables; never place secrets in frontend files.

## Production start-up checks

With `APP_ENV=production` the API refuses to start unless `JWT_SECRET` is a random value of 32+ characters, and unless an admin exists (or `ADMIN_EMAIL` and a 12+ character `ADMIN_PASSWORD` are supplied for the first start). Sample data and the development admin are never created in production (`SEED_DEMO_DATA=true` adds sample data for a staging copy). See `deployment/README.md`.
