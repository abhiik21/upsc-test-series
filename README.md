# UPSC Test Series Platform — v54

This package contains the public website, student portal, admin panel, API client, CMS screens, and a runnable FastAPI backend.

## Architecture

`public website → authentication → student portal → test engine → results/analytics`

`admin portal → question/test/CMS management → subscriptions/payments → notifications/reports/settings`

## Run locally with SQLite

```bash
cd backend
pip install -r requirements.txt
DB_BACKEND=sqlite uvicorn app:app --reload --port 8000
```

Serve the project root separately, for example:

```bash
python -m http.server 5500
```

Then open `login.html` and register a student. On a development machine only (`APP_ENV` not `production`), an admin account is created for testing:

`admin@example.com` / `AdminPass1!`

**This account does not exist in production.** A production server creates its first admin from `ADMIN_EMAIL` / `ADMIN_PASSWORD` and refuses to start while the development admin still has this password. See `deployment/README.md` for going live.

## Run with PostgreSQL

The backend now supports PostgreSQL through psycopg 3.

```bash
DB_BACKEND=postgres
DATABASE_URL=postgresql://upsc_user:password@localhost:5432/upsc
JWT_SECRET=<long-random-secret>
```

The runtime schema used by the active application is:

`backend/database/schema-runtime-postgres.sql`

The helper script is:

`backend/scripts/init_postgres.py`

The Docker deployment scaffold is under `deployment/`.

## Current production status

The repository and schema are prepared for PostgreSQL, but this build environment does not provide a running PostgreSQL server, so a live PostgreSQL connection could not be executed here. SQLite regression tests and the PostgreSQL adapter unit path were verified.

Real payment-gateway credentials, email/SMS/push providers, object storage, managed PostgreSQL backups, TLS and production secrets must be configured before accepting live traffic.


## v55 — Checkout

Open `checkout.html?plan_id=plan2` after logging in to test the development checkout. Set `PAYMENT_PROVIDER=development` for local simulation. Real gateway integration is intentionally disabled until a concrete provider adapter and production credentials are configured.
