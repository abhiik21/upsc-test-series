# v64 — Production safety

Found while preparing for a real deployment. All of these are fixed:

- **A well-known super-admin existed in production.** `admin@example.com` / `AdminPass1!` was created on every fresh database whatever the environment. Now it is created only outside production. In production the first admin comes from `ADMIN_EMAIL` / `ADMIN_PASSWORD` (12+ characters), and the server refuses to start if the old account still has the default password.
- **Demo data was created in production** (sample questions, tests, users, payments, coupons, blog posts). Now only in development, or when `SEED_DEMO_DATA=true`. Subjects, topics and the three plans are still created, because a real site needs them.
- **Unsafe secret**: production now refuses to start with the default or a short (<32 characters) `JWT_SECRET`.
- **Free "development" payments in production**: with `PAYMENT_PROVIDER=development` anyone could have activated a plan without paying. In production, checkout now answers 503 and development confirmation is disabled.
- **nginx served the whole project folder**, including `backend/app.py`, env templates, SQL files and the deployment files. Backend/deployment folders, dotfiles and source/config/document file types now return 404 (tested with a real nginx).
- **API address**: pages hard-coded `http://127.0.0.1:8000`, which cannot work on a real domain or in the Docker setup. On a real domain (or `localhost:8080`) the site now uses `/api/v1` on the same address; `localhost` on other ports still uses the local development API; `localStorage.upsc_api_base` overrides both.
- **Login throttle**: 10 wrong passwords per account per 15 minutes (in memory, per server process).
- Docker: web port bound to `127.0.0.1` by default (put HTTPS in front), `ADMIN_*` / `SEED_DEMO_DATA` passed through, `deployment/.env.example` added, `deployment/README.md` rewritten as a step-by-step guide.
- Docs: backend README no longer claims a Razorpay adapter exists.

Still missing before a paid launch: the payment gateway (see deployment/README.md).

Tests: `backend/tests_v64.py` starts the app in production mode in separate processes; v57/v58/v60–v64 pass.
