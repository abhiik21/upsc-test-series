# Putting the site on the internet

This guide takes you from an empty server to a running site. Read the **"Not ready yet"** box first.

> **Payments** use Razorpay. Follow section 5 to connect it and to test it with *test keys* before you use real money.

## 1. What you need

- A small Linux server (1–2 GB RAM is enough), e.g. Ubuntu 22.04/24.04, with Docker and Docker Compose installed.
- A domain name pointing to the server's IP address.
- An SMTP account for sign-up e-mails (or set `AUTH_REQUIRE_VERIFICATION=false` for now).
- A Razorpay account (test mode works immediately; live payments need Razorpay's KYC approval).

## 2. Copy the project and configure it

```bash
# copy the whole project folder to the server, e.g. to /opt/upsc, then:
cd /opt/upsc/deployment
cp .env.example .env
nano .env          # fill in JWT_SECRET, POSTGRES_PASSWORD, CORS_ORIGINS, ADMIN_EMAIL, ADMIN_PASSWORD, RAZORPAY_* ...
```

The server **refuses to start** in production if the secret is missing/short, if no first admin is given, or if the
old built-in `admin@example.com` account still has its default password. That is deliberate.

## 3. Start it

```bash
docker compose up -d --build
docker compose ps                 # all three services should be "healthy"/"running"
curl http://127.0.0.1:8080/health/ready
```

On the first start the database is created, the eight UPSC subjects and the three plans are added, and your admin
account is created from `ADMIN_EMAIL` / `ADMIN_PASSWORD`. No sample questions, tests or users are added.
After that first start you can remove `ADMIN_PASSWORD` from `.env`.

## 4. Add HTTPS (required)

The site is published only on `127.0.0.1:8080`, so put an HTTPS proxy in front. The simplest is **Caddy**
(it gets and renews the certificate by itself):

```
# /etc/caddy/Caddyfile
your-domain.example {
    reverse_proxy 127.0.0.1:8080
}
```

`sudo systemctl reload caddy`, then open `https://your-domain.example/admin-login.html`.
(Cloudflare in front of the server also works; keep the server's port 8080 closed to the internet either way.)

## 5. Connect Razorpay

1. In the Razorpay Dashboard generate **Test** API keys (Account & Settings → API Keys). Put them in `deployment/.env`:
   ```
   PAYMENT_PROVIDER=razorpay
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   RAZORPAY_WEBHOOK_SECRET=<a long random string you choose>
   ```
2. In the Dashboard add a **webhook** (Account & Settings → Webhooks):
   - URL: `https://your-domain.example/api/v1/payments/razorpay/webhook`
   - Secret: exactly the `RAZORPAY_WEBHOOK_SECRET` you chose
   - Events: `payment.captured`, `order.paid`, `payment.failed`
   The webhook is what activates a plan when a student pays but closes the browser before returning to the site.
3. Check the dashboard's payment-capture setting is **automatic**, otherwise payments stay "authorized" and are
   returned to the payer after a few days even though the site has already activated the plan.
4. `docker compose up -d`, then buy a plan yourself with a student account using Razorpay's test card/UPI details
   (see Razorpay's "test cards" documentation). Check that: the plan becomes active, premium tests unlock, the payment
   appears in **Admin → Payments**, and the webhook shows a successful delivery in the Razorpay dashboard.
5. Try a *failed* test payment and closing the payment window; nothing should be activated.
6. Go live: replace the keys with `rzp_live_...` keys, recreate the webhook in Live mode (same URL, new secret if you like),
   `docker compose up -d`, and make one real small purchase, then refund it from **Admin → Payments → Refund**
   (the refund is sent to Razorpay, not only recorded).

The server refuses to start with `PAYMENT_PROVIDER=razorpay` unless all three Razorpay settings are present, and it
logs a warning while test keys are in use. A coupon that reduces the price to zero activates the plan without a payment;
a total between ₹0 and ₹1 is refused because Razorpay's minimum is ₹1.

Not covered: tax invoices (the invoice numbers on payments are internal receipts), and cancelling a plan when a payment is
refunded. Check the tax and registration requirements that apply to you before charging students.

## 6. First things to do after logging in

1. Change nothing about the database by hand — use the admin pages.
2. **Test Management → + New Test Series**, then import questions (**Question Bank → Bulk Import**) and build tests.
3. Mark the tests you want everyone to try as **Free**; they appear on the public *Free Tests* page.
4. Check the public pages (Test Series with its plans, Free Tests, PYQs, Results) in a private browser window.

## 7. Backups and updates

- Back up the PostgreSQL volume regularly (your host's snapshot feature, or `docker compose exec postgres pg_dump ...`) and test a restore.
- Uploaded files (if you use local storage) live in `backend/data/uploads`; back them up too, or use S3 storage.
- To update: copy the new project files over the old ones (keep `deployment/.env`), then `docker compose up -d --build`.

## 8. Good to know

- Login is limited to 10 wrong passwords per account per 15 minutes.
- The web server hides the backend source code, configuration, databases and documents from the internet.
- `/docs` (API documentation) is switched off in production.
- Running on `localhost:5500` (Live Server) or opening files directly still talks to a local API on port 8000, for development.
