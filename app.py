from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import sqlite3

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # SQLite-only development remains supported
    psycopg = None
    dict_row = None
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from contextlib import asynccontextmanager

import jwt
from fastapi import Body, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from starlette.responses import FileResponse

from services.notifications import OTP_TTL_MINUTES, generate_otp, send_email, smtp_configured
from services.storage import MAX_UPLOAD_MB, signed_url, store
from services.observability import RequestContextMiddleware, logger
from services.question_import import MAX_QUESTIONS_PER_IMPORT, ImportFileError, normalise_stem, parse_csv, parse_docx, slugify
from payment.razorpay_provider import PaymentGatewayError, RazorpayConfig, RazorpayProvider

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("DEV_SQLITE_PATH", DATA_DIR / "upsc_dev.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_BACKEND = os.getenv("DB_BACKEND", "postgres" if DATABASE_URL else "sqlite").lower()
DEFAULT_JWT_SECRET = "dev-only-change-me-use-a-long-random-secret-32chars-minimum"
JWT_SECRET = os.getenv("JWT_SECRET", DEFAULT_JWT_SECRET)
IS_PRODUCTION = os.getenv("APP_ENV", "development").lower() == "production"
# Demo content (sample questions, tests, users, payments, coupons...) is only created outside production,
# unless SEED_DEMO_DATA=true is set explicitly (for a staging copy).
SEED_DEMO = (not IS_PRODUCTION) or os.getenv("SEED_DEMO_DATA", "false").lower() == "true"


def validate_production_config() -> None:
    """Refuse to start in production with settings that would leave the site open."""
    if not IS_PRODUCTION:
        return
    if JWT_SECRET == DEFAULT_JWT_SECRET or len(JWT_SECRET) < 32:
        raise RuntimeError("Unsafe production configuration: JWT_SECRET must be set to a random value of at least 32 characters.")
    provider = os.getenv("PAYMENT_PROVIDER", "development").strip().lower()
    if provider == "development":
        logger.warning("PAYMENT_PROVIDER=development: online payments are switched off in production. Set PAYMENT_PROVIDER=razorpay to sell plans.")
    elif provider == "razorpay":
        cfg = RazorpayConfig.from_env()
        missing = [n for n, v in (("RAZORPAY_KEY_ID", cfg.key_id), ("RAZORPAY_KEY_SECRET", cfg.key_secret), ("RAZORPAY_WEBHOOK_SECRET", cfg.webhook_secret)) if not v]
        if missing:
            raise RuntimeError("Unsafe production configuration: PAYMENT_PROVIDER=razorpay needs " + ", ".join(missing) + ".")
        if cfg.key_id.startswith("rzp_test_"):
            logger.warning("Razorpay TEST keys are in use: payments are simulated and no real money moves.")
    else:
        raise RuntimeError(f"Unsafe production configuration: PAYMENT_PROVIDER={provider!r} is not supported (use 'razorpay').")
    if os.getenv("AUTH_REQUIRE_VERIFICATION", "false").strip().lower() == "true" and not smtp_configured():
        raise RuntimeError("Unsafe production configuration: AUTH_REQUIRE_VERIFICATION=true needs SMTP_HOST and EMAIL_FROM (students would be unable to complete sign-up).")
JWT_ALG = "HS256"
ACCESS_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))

app = FastAPI(
    title="UPSC Test Series API",
    version="0.1.0",
    docs_url=None if os.getenv("APP_ENV", "development").lower() == "production" else "/docs",
    redoc_url=None if os.getenv("APP_ENV", "development").lower() == "production" else "/redoc",
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

bearer = HTTPBearer(auto_error=False)

DDL = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY,
 first_name TEXT NOT NULL,
 last_name TEXT,
 email TEXT UNIQUE,
 mobile TEXT UNIQUE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'student',
 status TEXT NOT NULL DEFAULT 'active',
 target_exam_year INTEGER,
 preparation_stage TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS subjects (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 display_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS topics (
 id TEXT PRIMARY KEY,
 subject_id TEXT NOT NULL REFERENCES subjects(id),
 name TEXT NOT NULL,
 slug TEXT NOT NULL,
 UNIQUE(subject_id, slug)
);
CREATE TABLE IF NOT EXISTS questions (
 id TEXT PRIMARY KEY,
 subject_id TEXT,
 topic_id TEXT,
 stem TEXT NOT NULL,
 option_a TEXT NOT NULL,
 option_b TEXT NOT NULL,
 option_c TEXT NOT NULL,
 option_d TEXT NOT NULL,
 correct_option TEXT NOT NULL,
 explanation TEXT,
 difficulty TEXT NOT NULL DEFAULT 'moderate',
 upsc_year INTEGER,
 status TEXT NOT NULL DEFAULT 'published',
 source TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_series (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 description TEXT,
 price_paise INTEGER NOT NULL DEFAULT 0,
 validity_days INTEGER,
 status TEXT NOT NULL DEFAULT 'published',
 featured INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tests (
 id TEXT PRIMARY KEY,
 series_id TEXT,
 title TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 description TEXT,
 test_type TEXT NOT NULL,
 duration_minutes INTEGER NOT NULL,
 total_questions INTEGER NOT NULL,
 total_marks REAL NOT NULL,
 negative_mark REAL NOT NULL DEFAULT 0,
 access_type TEXT NOT NULL DEFAULT 'premium',
 scheduled_at TEXT,
 status TEXT NOT NULL DEFAULT 'published',
 required_tier INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS test_questions (
 test_id TEXT NOT NULL,
 question_id TEXT NOT NULL,
 question_order INTEGER NOT NULL,
 marks REAL NOT NULL,
 negative_marks REAL NOT NULL DEFAULT 0,
 PRIMARY KEY(test_id, question_id)
);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 test_id TEXT NOT NULL,
 started_at TEXT NOT NULL,
 submitted_at TEXT,
 status TEXT NOT NULL DEFAULT 'in_progress',
 score REAL,
 correct INTEGER DEFAULT 0,
 incorrect INTEGER DEFAULT 0,
 unattempted INTEGER DEFAULT 0,
 time_taken_seconds INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS answers (
 id TEXT PRIMARY KEY,
 attempt_id TEXT NOT NULL,
 question_id TEXT NOT NULL,
 selected_option TEXT,
 is_correct INTEGER,
 marked_for_review INTEGER NOT NULL DEFAULT 0,
 time_spent_seconds INTEGER NOT NULL DEFAULT 0,
 UNIQUE(attempt_id, question_id)
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 price_paise INTEGER NOT NULL DEFAULT 0,
 validity_days INTEGER NOT NULL,
 is_public INTEGER NOT NULL DEFAULT 1,
 tier INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 plan_id TEXT NOT NULL,
 status TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 payment_id TEXT
);
CREATE TABLE IF NOT EXISTS bookmarks (
 user_id TEXT NOT NULL,
 content_type TEXT NOT NULL,
 content_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 PRIMARY KEY(user_id, content_type, content_id)
);
CREATE TABLE IF NOT EXISTS mistakes (
 user_id TEXT NOT NULL,
 question_id TEXT NOT NULL,
 created_at TEXT NOT NULL,
 note TEXT,
 resolved INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id, question_id)
);
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 title TEXT NOT NULL,
 body TEXT NOT NULL,
 type TEXT NOT NULL,
 is_read INTEGER NOT NULL DEFAULT 0,
 href TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_affairs (
 id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 category TEXT NOT NULL,
 gs TEXT,
 month TEXT,
 published_at TEXT NOT NULL,
 minutes INTEGER NOT NULL DEFAULT 5,
 summary TEXT NOT NULL,
 key_points TEXT,
 status TEXT NOT NULL DEFAULT 'published'
);
CREATE TABLE IF NOT EXISTS study_material (
 id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 subject TEXT NOT NULL,
 topic TEXT,
 format TEXT NOT NULL,
 access_type TEXT NOT NULL DEFAULT 'free',
 pages TEXT,
 summary TEXT NOT NULL,
 progress INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'published'
);
CREATE TABLE IF NOT EXISTS blog_posts (
 id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 slug TEXT UNIQUE NOT NULL,
 excerpt TEXT,
 category TEXT,
 author TEXT,
 status TEXT NOT NULL DEFAULT 'published',
 published_at TEXT
);
CREATE TABLE IF NOT EXISTS support_enquiries (
 id TEXT PRIMARY KEY,
 user_id TEXT,
 name TEXT NOT NULL,
 email TEXT,
 subject TEXT NOT NULL,
 message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open',
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 plan_id TEXT,
 transaction_id TEXT UNIQUE NOT NULL,
 order_id TEXT,
 invoice_number TEXT,
 gateway TEXT NOT NULL DEFAULT 'razorpay',
 method TEXT NOT NULL DEFAULT 'upi',
 amount_paise INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'successful',
 created_at TEXT NOT NULL,
 refunded_at TEXT,
 refund_amount_paise INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS checkout_orders (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 plan_id TEXT NOT NULL,
 coupon_id TEXT,
 subtotal_paise INTEGER NOT NULL,
 discount_paise INTEGER NOT NULL DEFAULT 0,
 total_paise INTEGER NOT NULL,
 currency TEXT NOT NULL DEFAULT 'INR',
 provider TEXT NOT NULL DEFAULT 'development',
 provider_order_id TEXT UNIQUE NOT NULL,
 status TEXT NOT NULL DEFAULT 'created',
 created_at TEXT NOT NULL,
 paid_at TEXT
);

CREATE TABLE IF NOT EXISTS otp_challenges (
 id TEXT PRIMARY KEY,
 identifier TEXT NOT NULL,
 purpose TEXT NOT NULL,
 code_hash TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 consumed_at TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otp_identifier_created ON otp_challenges(identifier, created_at);
CREATE TABLE IF NOT EXISTS password_reset_tokens (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 token_hash TEXT NOT NULL UNIQUE,
 expires_at TEXT NOT NULL,
 used_at TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_assets (
 id TEXT PRIMARY KEY,
 storage_key TEXT NOT NULL UNIQUE,
 original_name TEXT NOT NULL,
 content_type TEXT NOT NULL,
 size_bytes INTEGER NOT NULL,
 uploaded_by TEXT NOT NULL,
 access_type TEXT NOT NULL DEFAULT 'private',
 required_plan_id TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_assets_uploader ON file_assets(uploaded_by, created_at);
CREATE TABLE IF NOT EXISTS notification_deliveries (
 id TEXT PRIMARY KEY,
 notification_id TEXT NOT NULL,
 channel TEXT NOT NULL,
 provider TEXT NOT NULL,
 status TEXT NOT NULL,
 error_message TEXT,
 delivered_at TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_notification ON notification_deliveries(notification_id);

CREATE TABLE IF NOT EXISTS coupons (
 id TEXT PRIMARY KEY,
 code TEXT UNIQUE NOT NULL,
 campaign TEXT,
 discount_type TEXT NOT NULL DEFAULT 'percentage',
 value REAL NOT NULL DEFAULT 0,
 applies_to TEXT NOT NULL DEFAULT 'all',
 starts_at TEXT,
 ends_at TEXT,
 redemption_limit INTEGER,
 per_user_limit INTEGER NOT NULL DEFAULT 1,
 min_order_paise INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS coupon_redemptions (
 id TEXT PRIMARY KEY,
 coupon_id TEXT NOT NULL,
 user_id TEXT NOT NULL,
 order_id TEXT,
 discount_paise INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompatRow(dict):
    """Dict-like row that also supports SQLite-style numeric indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        return CompatRow(row) if isinstance(row, dict) else row

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [CompatRow(r) if isinstance(r, dict) else r for r in rows]

    @property
    def rowcount(self):
        return self._cursor.rowcount


class Database:
    def __init__(self, conn, backend: str):
        self.conn = conn
        self.backend = backend

    def _sql(self, sql: str) -> str:
        if self.backend != "postgres":
            return sql
        # The application uses DB-agnostic qmark placeholders; psycopg uses %s.
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: Iterable[Any] = ()):
        if self.backend == "postgres":
            return PostgresCursor(self.conn.execute(self._sql(sql), params))
        return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]):
        if self.backend == "postgres":
            return PostgresCursor(self.conn.executemany(self._sql(sql), list(seq_of_params)))
        return self.conn.executemany(sql, seq_of_params)

    def executescript(self, script: str):
        statements = [part.strip() for part in script.split(";") if part.strip()]
        for statement in statements:
            self.execute(statement)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db() -> Database:
    if DB_BACKEND == "postgres":
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is required when DB_BACKEND=postgres")
        if psycopg is None:
            raise RuntimeError("psycopg is not installed; run pip install -r requirements.txt")
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        return Database(raw, "postgres")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return Database(conn, "sqlite")


def db_error_types():
    errors = [sqlite3.Error]
    if psycopg is not None:
        errors.append(psycopg.Error)
    return tuple(errors)


DB_ERROR = db_error_types()
DB_INTEGRITY_ERROR = (sqlite3.IntegrityError,) + ((psycopg.IntegrityError,) if psycopg is not None else ())

def ensure_column(conn: Database, table: str, column: str, definition: str) -> None:
    if conn.backend == "postgres":
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_dev_schema(conn: Database) -> None:
    current_cols = {
        "users": {
            "email_verified_at":"TEXT"
        },
        "file_assets": {
            "required_plan_id":"TEXT"
        },
        "plans": {
            "tier":"INTEGER NOT NULL DEFAULT 1"
        },
        "tests": {
            "required_tier":"INTEGER NOT NULL DEFAULT 1"
        },
        "subscriptions": {
            "payment_id":"TEXT"
        },
        "current_affairs": {
            "author":"TEXT", "source":"TEXT", "featured":"INTEGER NOT NULL DEFAULT 0",
            "mcq_link":"TEXT", "revision":"INTEGER NOT NULL DEFAULT 0", "seo":"INTEGER NOT NULL DEFAULT 0",
            "content":"TEXT"
        },
        "study_material": {
            "owner":"TEXT", "tags":"TEXT", "source":"TEXT", "featured":"INTEGER NOT NULL DEFAULT 0",
            "track":"INTEGER NOT NULL DEFAULT 0", "practice":"TEXT", "seo":"INTEGER NOT NULL DEFAULT 0", "file_url":"TEXT"
        },
        "blog_posts": {
            "format":"TEXT", "image":"TEXT", "seo_title":"TEXT", "meta_description":"TEXT",
            "featured":"INTEGER NOT NULL DEFAULT 0", "newsletter":"INTEGER NOT NULL DEFAULT 0",
            "related_tests":"TEXT", "revision":"INTEGER NOT NULL DEFAULT 0", "allow_comments":"INTEGER NOT NULL DEFAULT 0",
            "content":"TEXT"
        },
    }
    for table, cols in current_cols.items():
        for column, definition in cols.items():
            ensure_column(conn, table, column, definition)

def ensure_production_admin(conn: Database) -> None:
    """Production never ships a well-known admin. The first admin comes from ADMIN_EMAIL / ADMIN_PASSWORD."""
    dev = conn.execute("SELECT password_hash FROM users WHERE lower(email)='admin@example.com'").fetchone()
    if dev and verify_password("AdminPass1!", dev["password_hash"]):
        raise RuntimeError("The built-in development admin (admin@example.com) still has its default password. Change or delete that account before running in production.")
    if conn.execute("SELECT COUNT(*) AS n FROM users WHERE role IN ('admin','super_admin') AND status='active'").fetchone()["n"]:
        return
    email, password = os.getenv("ADMIN_EMAIL", "").strip().lower(), os.getenv("ADMIN_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("No admin account exists yet. Set ADMIN_EMAIL and ADMIN_PASSWORD (at least 12 characters) for the first start.")
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD must be at least 12 characters long.")
    ts = now_iso()
    conn.execute("INSERT INTO users(id,first_name,last_name,email,mobile,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 (secrets.token_hex(12), os.getenv("ADMIN_NAME", "Admin").strip() or "Admin", None, email, os.getenv("ADMIN_MOBILE") or None, hash_password(password), "super_admin", "active", ts, ts))
    conn.commit()
    logger.info("first admin account created for %s", email)


def init_db() -> None:
    conn = get_db()
    locked = False
    try:
        if conn.backend == "postgres":
            conn.execute("SELECT pg_advisory_lock(hashtext('upsc-test-series-init'))")
            locked = True
        conn.executescript(DDL)
        migrate_dev_schema(conn)
        # Development seed only.
        if conn.execute("SELECT COUNT(*) FROM subjects").fetchone()[0] == 0:
            subjects = [
                ("polity", "Indian Polity", "indian-polity", 1),
                ("economy", "Economy", "economy", 2),
                ("history", "History", "history", 3),
                ("geography", "Geography", "geography", 4),
                ("environment", "Environment", "environment", 5),
                ("science", "Science & Technology", "science-technology", 6),
                ("current", "Current Affairs", "current-affairs", 7),
                ("csat", "CSAT", "csat", 8),
            ]
            conn.executemany("INSERT INTO subjects VALUES (?, ?, ?, ?)", subjects)
            topics = [
                ("polity-parliament", "polity", "Parliament", "parliament"),
                ("polity-rights", "polity", "Fundamental Rights", "fundamental-rights"),
                ("economy-monetary", "economy", "Monetary Policy", "monetary-policy"),
                ("environment-biodiversity", "environment", "Biodiversity", "biodiversity"),
            ]
            conn.executemany("INSERT INTO topics VALUES (?, ?, ?, ?)", topics)
            plans = [("plan1", "Prelims Starter", "prelims-starter", 49900, 180, 1, 1), ("plan2", "Prelims Complete", "prelims-complete", 99900, 365, 1, 2), ("plan3", "Prelims + Current Affairs", "prelims-current-affairs", 149900, 365, 1, 3)]
            conn.executemany("INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?)", plans)
            if SEED_DEMO:
                ts = now_iso()
                q = [
                    ("q1", "polity", "polity-rights", "Which Article guarantees equality before the law?", "Article 12", "Article 14", "Article 16", "Article 19", "B", "Article 14 guarantees equality before the law and equal protection of laws.", "easy", 2024, "published", "Constitution of India", ts, ts),
                    ("q2", "economy", "economy-monetary", "Which institution conducts monetary policy in India?", "SEBI", "RBI", "NITI Aayog", "Finance Commission", "B", "The Reserve Bank of India is responsible for monetary policy formulation and implementation.", "moderate", 2023, "published", "RBI", ts, ts),
                    ("q3", "environment", "environment-biodiversity", "Which term is most closely associated with species diversity?", "Biodiversity", "Fiscal deficit", "Demographic dividend", "Current account", "A", "Biodiversity describes variability among living organisms.", "easy", 2022, "published", "NCERT", ts, ts),
                ]
                for row in q:
                    conn.execute("INSERT INTO questions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
                conn.execute("INSERT INTO test_series VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("series1", "UPSC Prelims 2027", "upsc-prelims-2027", "Comprehensive General Studies test series.", 99900, 365, "published", 1))
                tests = [
                    ("test1", "series1", "Polity Foundation Test", "polity-foundation-test", "Core polity concepts.", "subject", 30, 3, 6, 0.67, "premium", None, "published", 1),
                    ("test2", "series1", "Mixed GS Mini Test", "mixed-gs-mini-test", "Mixed practice test.", "mixed", 30, 3, 6, 0.67, "premium", None, "published", 2),
                ]
                conn.executemany("INSERT INTO tests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tests)
                tq = [("test1", "q1", 1, 2, 0.67), ("test2", "q1", 1, 2, 0.67), ("test2", "q2", 2, 2, 0.67), ("test2", "q3", 3, 2, 0.67)]
                conn.executemany("INSERT INTO test_questions VALUES (?, ?, ?, ?, ?)", tq)
                ca = [
                    ("ca1", "Parliamentary committees and legislative scrutiny", "Polity & Governance", "GS-II", "September 2026", "2026-09-18T08:00:00+00:00", 5, "Revision note on parliamentary committees, legislative scrutiny and departmental oversight.", "Composition, functions, reports and relationship with floor discussion.", "published"),
                    ("ca2", "Inflation indicators and monetary-policy transmission", "Economy", "GS-III", "September 2026", "2026-09-18T06:00:00+00:00", 6, "Concept-focused reading on inflation measurement and monetary-policy transmission.", "CPI/WPI, headline vs core inflation and transmission channels.", "published"),
                    ("ca3", "Biodiversity conservation and protected-area governance", "Environment", "GS-III", "September 2026", "2026-09-17T07:00:00+00:00", 5, "Structured note on conservation frameworks and protected-area concepts.", "In-situ, ex-situ and institutional roles.", "published")
                ]
                conn.executemany("INSERT INTO current_affairs(id,title,category,gs,month,published_at,minutes,summary,key_points,status) VALUES (?,?,?,?,?,?,?,?,?,?)", ca)
                material = [
                    ("mat1", "Parliament & Constitutional Bodies", "Polity", "Parliament", "Notes", "premium", "42 pages", "Structured notes covering Parliament, committees and constitutional bodies.", 68, "published"),
                    ("mat2", "Constitutional Articles — Rapid Revision Sheet", "Polity", "Constitution", "Revision Sheet", "free", "8 pages", "Quick-reference sheet for important Articles and constitutional provisions.", 100, "published"),
                    ("mat3", "Monetary Policy & Inflation", "Economy", "Monetary Policy", "PDF", "premium", "31 pages", "Concept notes on inflation, monetary transmission and policy tools.", 42, "published"),
                    ("mat4", "Biodiversity & Protected Areas", "Environment", "Biodiversity", "Revision Sheet", "premium", "14 pages", "High-yield environment revision sheet linked to protected areas.", 47, "published")
                ]
                conn.executemany("INSERT INTO study_material(id,title,subject,topic,format,access_type,pages,summary,progress,status) VALUES (?,?,?,?,?,?,?,?,?,?)", material)
                blog = [
                    ("blog1", "How to analyse a UPSC mock test", "how-to-analyse-a-upsc-mock-test", "A practical framework for reviewing accuracy, time use and recurring mistakes.", "Preparation", "Editorial Desk", "published", "2026-09-18T05:00:00+00:00"),
                    ("blog2", "Using UPSC PYQs effectively", "using-upsc-pyqs-effectively", "Turn previous year questions into a structured revision tool.", "PYQ Analysis", "Editorial Desk", "published", "2026-09-15T05:00:00+00:00")
                ]
                conn.executemany("INSERT INTO blog_posts(id,title,slug,excerpt,category,author,status,published_at) VALUES (?,?,?,?,?,?,?,?)", blog)
                # Commerce/operations demo seed records. These are safe to recreate only on a fresh development DB.
                demo_student = conn.execute("SELECT id FROM users WHERE role='student' ORDER BY created_at LIMIT 1").fetchone()
                demo_user = demo_student[0] if demo_student else "dev-student"
                payments = [
                    ("pay1", demo_user, "plan2", "TXN-20260918-001", "ORD-20260918-001", "INV-2026-001", "razorpay", "upi", 99900, "successful", ts, None, 0),
                    ("pay2", demo_user, "plan3", "TXN-20260917-002", "ORD-20260917-002", "INV-2026-002", "razorpay", "card", 149900, "successful", ts, None, 0),
                    ("pay3", demo_user, "plan1", "TXN-20260916-003", "ORD-20260916-003", "INV-2026-003", "cashfree", "upi", 49900, "pending", ts, None, 0),
                    ("pay4", demo_user, "plan1", "TXN-20260915-004", "ORD-20260915-004", "INV-2026-004", "razorpay", "netbanking", 49900, "failed", ts, None, 0),
                    ("pay5", demo_user, "plan2", "TXN-20260914-005", "ORD-20260914-005", "INV-2026-005", "razorpay", "upi", 99900, "refunded", ts, ts, 99900),
                ]
                conn.executemany("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", payments)
                coupons = [
                    ("cp1","PRELIMS25","September Launch","percentage",25,"plan2","2026-09-01T00:00:00+00:00","2026-09-30T23:59:59+00:00",5000,1,0,"active"),
                    ("cp2","GS100","GS Focus","flat",100,"plan1","2026-09-10T00:00:00+00:00","2026-10-15T23:59:59+00:00",1000,1,39900,"active"),
                    ("cp3","WELCOME10","New Student","percentage",10,"all","2026-10-01T00:00:00+00:00","2026-11-01T23:59:59+00:00",5000,1,0,"scheduled"),
                    ("cp4","AUGUST15","August Campaign","percentage",15,"all","2026-08-01T00:00:00+00:00","2026-08-31T23:59:59+00:00",1000,1,0,"expired"),
                    ("cp5","PAUSE20","Paused","percentage",20,"plan3","2026-09-01T00:00:00+00:00","2026-12-31T23:59:59+00:00",500,1,0,"paused"),
                ]
                conn.executemany("INSERT INTO coupons VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", coupons)
            conn.commit()
        for pid, tier in (("plan1", 1), ("plan2", 2), ("plan3", 3)):
            conn.execute("UPDATE plans SET tier=? WHERE id=? AND tier=1", (tier, pid))
        conn.commit()
        if IS_PRODUCTION:
            ensure_production_admin(conn)
        # Development only: keep one admin account available for API smoke tests.
        elif conn.execute("SELECT COUNT(*) FROM users WHERE email='admin@example.com'").fetchone()[0] == 0:
            ts = now_iso()
            conn.execute("INSERT INTO users(id,first_name,last_name,email,mobile,password_hash,role,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         ("dev-admin", "Admin", "User", "admin@example.com", "9000000000", hash_password("AdminPass1!"), "super_admin", "active", ts, ts))
            conn.commit()
    
        conn.commit()
    finally:
        if locked:
            try:
                conn.execute("SELECT pg_advisory_unlock(hashtext('upsc-test-series-init'))")
                conn.commit()
            except Exception:
                conn.rollback()
        conn.close()
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def make_token(user: Any) -> str:
    payload = {"sub": user["id"], "role": user["role"], "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Any:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if not user or user["status"] != "active":
        raise HTTPException(status_code=401, detail="User account is not active")
    return user


def require_admin(user: Any = Depends(get_current_user)) -> Any:
    if user["role"] not in {"admin", "super_admin", "content_manager", "evaluator", "finance", "support"}:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


class RegisterBody(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    email: EmailStr | None = None
    mobile: str | None = Field(default=None, min_length=7, max_length=20)
    password: str = Field(min_length=8, max_length=128)
    target_exam_year: int | None = None
    preparation_stage: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.email and not self.mobile:
            raise ValueError("Email or mobile is required")


class LoginBody(BaseModel):
    identifier: str
    password: str


class AnswerBody(BaseModel):
    selected_option: str | None = Field(default=None, pattern="^[A-Ea-e]$")
    marked_for_review: bool = False
    time_spent_seconds: int = Field(default=0, ge=0)


class ProfileUpdateBody(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    mobile: str | None = Field(default=None, min_length=7, max_length=20)
    target_exam_year: int | None = None
    preparation_stage: str | None = None


class NotificationCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=500)
    type: str = Field(default="system", max_length=40)
    href: str | None = None
    channel: str = Field(default='in_app', pattern='^(in_app|email|both)$')


class SupportBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr | None = None
    subject: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=3000)


class QuestionCreateBody(BaseModel):
    subject_id: str | None = None
    topic_id: str | None = None
    stem: str = Field(min_length=1)
    option_a: str = Field(min_length=1)
    option_b: str = Field(min_length=1)
    option_c: str = Field(min_length=1)
    option_d: str = Field(min_length=1)
    correct_option: str = Field(pattern='^[A-Da-d]$')
    explanation: str | None = None
    difficulty: str = 'moderate'
    upsc_year: int | None = None
    source: str | None = None
    status: str = 'draft'


class TestCreateBody(BaseModel):
    series_id: str | None = None
    title: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    description: str | None = None
    test_type: str = 'mixed'
    duration_minutes: int = Field(gt=0)
    total_marks: float = Field(gt=0)
    negative_mark: float = Field(ge=0)
    access_type: str = 'premium'
    required_tier: int = Field(default=1, ge=1, le=10)
    scheduled_at: str | None = None
    status: str = 'draft'
    question_ids: list[str] = []


class SeriesCreateBody(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    description: str | None = None
    price_paise: int = Field(ge=0)
    validity_days: int | None = Field(default=None, gt=0)
    status: str = 'draft'
    featured: bool = False


class StudentUpdateBody(BaseModel):
    status: str | None = None
    role: str | None = None
    target_exam_year: int | None = None
    preparation_stage: str | None = None


class SettingsUpdateBody(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = ''


class PlanCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=120)
    price_paise: int = Field(ge=0)
    validity_days: int = Field(gt=0)
    is_public: bool = True

class RefundBody(BaseModel):
    payment_id: str
    amount_paise: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=500)

class CouponCreateBody(BaseModel):
    code: str = Field(min_length=2, max_length=40)
    campaign: str | None = None
    discount_type: str = 'percentage'
    value: float = Field(ge=0)
    applies_to: str = 'all'
    starts_at: str | None = None
    ends_at: str | None = None
    redemption_limit: int | None = Field(default=None, ge=1)
    per_user_limit: int = Field(default=1, ge=1)
    min_order_paise: int = Field(default=0, ge=0)
    status: str = 'active'

class CouponApplyBody(BaseModel):
    plan_id: str
    code: str = Field(min_length=2, max_length=40)

class CheckoutOrderBody(BaseModel):
    plan_id: str
    coupon_code: str | None = None

class DevPaymentConfirmBody(BaseModel):
    order_id: str
    method: str = Field(default='upi', max_length=50)


class CurrentAffairBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    gs: str | None = None
    month: str | None = None
    published_at: str | None = None
    minutes: int = Field(default=5, ge=1, le=120)
    summary: str = Field(min_length=1)
    key_points: str | None = None
    status: str = 'draft'
    author: str | None = None
    source: str | None = None
    featured: bool = False
    mcq_link: str | None = None
    revision: bool = False
    seo: bool = False
    content: str | None = None


class StudyMaterialBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=120)
    topic: str | None = None
    format: str = 'PDF'
    access_type: str = 'free'
    pages: str | None = None
    summary: str = Field(min_length=1)
    status: str = 'draft'
    owner: str | None = None
    tags: str | None = None
    source: str | None = None
    featured: bool = False
    track: bool = False
    practice: str | None = None
    seo: bool = False
    file_url: str | None = None


class BlogPostBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=280)
    excerpt: str | None = None
    category: str | None = None
    author: str | None = None
    status: str = 'draft'
    published_at: str | None = None
    format: str | None = None
    image: str | None = None
    seo_title: str | None = None
    meta_description: str | None = None
    featured: bool = False
    newsletter: bool = False
    related_tests: str | None = None
    revision: bool = False
    allow_comments: bool = False
    content: str | None = None


class CurrentAffairBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    gs: str | None = None
    month: str | None = None
    published_at: str | None = None
    minutes: int = Field(default=5, ge=1, le=120)
    summary: str = Field(min_length=1)
    key_points: str | None = None
    status: str = 'draft'
    author: str | None = None
    source: str | None = None
    featured: bool = False
    mcq_link: str | None = None
    revision: bool = False
    seo: bool = False
    content: str | None = None


class StudyMaterialBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=120)
    topic: str | None = None
    format: str = 'PDF'
    access_type: str = 'free'
    pages: str | None = None
    summary: str = Field(min_length=1)
    status: str = 'draft'
    owner: str | None = None
    tags: str | None = None
    source: str | None = None
    featured: bool = False
    track: bool = False
    practice: str | None = None
    seo: bool = False
    file_url: str | None = None


class BlogPostBody(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=280)
    excerpt: str | None = None
    category: str | None = None
    author: str | None = None
    status: str = 'draft'
    published_at: str | None = None
    format: str | None = None
    image: str | None = None
    seo_title: str | None = None
    meta_description: str | None = None
    featured: bool = False
    newsletter: bool = False
    related_tests: str | None = None
    revision: bool = False
    allow_comments: bool = False
    content: str | None = None


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def utc_from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def emit_notification(conn: Database, user: Any, title: str, body: str, notification_type: str, href: str | None = None, email_subject: str | None = None) -> dict[str, Any]:
    notification_id = secrets.token_hex(12)
    ts = now_iso()
    conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)", (notification_id, user["id"], title, body, notification_type, 0, href, ts))
    delivery = {"status": "skipped", "provider": "none"}
    if user["email"] and os.getenv("SMTP_HOST") and os.getenv("EMAIL_FROM"):
        result = send_email(str(user["email"]), email_subject or title, body)
        delivery = {"status": "delivered" if result.delivered else "failed", "provider": result.provider}
        conn.execute("INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?)", (secrets.token_hex(12), notification_id, result.channel, result.provider, delivery["status"], None if result.delivered else result.message, ts if result.delivered else None, ts))
    return {"notification_id": notification_id, "delivery": delivery}


class OTPRequestBody(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    purpose: str = Field(default='email_verify', pattern='^(email_verify|password_reset)$')


class OTPVerifyBody(BaseModel):
    identifier: str = Field(min_length=3, max_length=254)
    purpose: str = Field(default='email_verify', pattern='^(email_verify|password_reset)$')
    code: str = Field(min_length=6, max_length=6, pattern='^\\d{6}$')


class PasswordResetBody(BaseModel):
    reset_token: str = Field(min_length=32, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class RegisterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    first_name: str
    last_name: str | None
    email: str | None
    mobile: str | None
    role: str
    status: str


def public_user(user: Any) -> dict[str, Any]:
    return {k: user[k] for k in ("id", "first_name", "last_name", "email", "mobile", "role", "status", "target_exam_year", "preparation_stage")}


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_config()
    init_db()
    yield

app.router.lifespan_context = lifespan


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "ok", "service": "upsc-test-series-api"}


@app.get("/health/ready")
def health_ready() -> dict[str, str]:
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ready", "service": "upsc-test-series-api", "database": conn.backend}
    except Exception as exc:
        logger.exception("readiness_check_failed")
        raise HTTPException(status_code=503, detail="Database is not ready") from exc
    finally:
        conn.close()


@app.get("/health")
def health() -> dict[str, str]:
    conn = get_db()
    try:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "service": "upsc-test-series-api", "database": conn.backend}
    finally:
        conn.close()


@app.post("/api/v1/auth/register")
def register(body: RegisterBody) -> dict[str, Any]:
    conn = get_db()
    existing = None
    if body.email:
        existing = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (str(body.email),)).fetchone()
    if not existing and body.mobile:
        existing = conn.execute("SELECT id FROM users WHERE mobile=?", (body.mobile,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=409, detail="An account already exists with this email/mobile")
    user_id = secrets.token_hex(16)
    ts = now_iso()
    requires_verification = os.getenv("AUTH_REQUIRE_VERIFICATION", "false").lower() == "true" and bool(body.email)
    account_status = "pending_verification" if requires_verification else "active"
    conn.execute(
        "INSERT INTO users(id,first_name,last_name,email,mobile,password_hash,role,status,target_exam_year,preparation_stage,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, body.first_name.strip(), (body.last_name or "").strip() or None, str(body.email) if body.email else None, body.mobile, hash_password(body.password), "student", account_status, body.target_exam_year, body.preparation_stage, ts, ts),
    )
    conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)", (secrets.token_hex(12), user_id, "Welcome to your UPSC student portal", "Your dashboard, tests, PYQs, current affairs and performance tools are ready.", "account", 0, "student-dashboard.html", ts))
    if requires_verification:
        code = generate_otp(); expires = (datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
        conn.execute("INSERT INTO otp_challenges VALUES (?,?,?,?,?,?,?,?)", (secrets.token_hex(12), str(body.email), "email_verify", hash_token(code), expires, 0, None, ts))
        result = send_email(str(body.email), "Verify your UPSC Test Series account", f"Your verification code is {code}. It expires in {OTP_TTL_MINUTES} minutes.")
        if os.getenv('APP_ENV','development') == 'production' and not result.delivered:
            conn.rollback(); conn.close(); raise HTTPException(503, "Email delivery is temporarily unavailable")
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    if requires_verification:
        out = {"user": public_user(user), "verification_required": True, "token_type": "bearer"}
        if os.getenv('APP_ENV','development') != 'production' and os.getenv('DEV_SHOW_OTP','true').lower() == 'true': out['development_otp'] = code
        return out
    return {"user": public_user(user), "access_token": make_token(user), "token_type": "bearer"}


@app.post("/api/v1/auth/request-otp")
def request_otp(body: OTPRequestBody) -> dict[str, Any]:
    conn = get_db()
    identifier = body.identifier.strip()
    user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) OR mobile=?", (identifier, identifier)).fetchone()
    if body.purpose == 'password_reset' and not user:
        conn.close()
        # Avoid account enumeration.
        return {"sent": True, "message": "If an account matches, a verification code has been sent."}
    recent = conn.execute("SELECT COUNT(*) FROM otp_challenges WHERE identifier=? AND created_at>=?", (identifier, (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat())).fetchone()[0]
    if recent >= 5:
        conn.close(); raise HTTPException(429, "Too many OTP requests. Please try again later.")
    code = generate_otp()
    ts = now_iso(); expires = (datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)).isoformat()
    conn.execute("INSERT INTO otp_challenges VALUES (?,?,?,?,?,?,?,?)", (secrets.token_hex(12), identifier, body.purpose, hash_token(code), expires, 0, None, ts))
    # Email delivery is the production channel here; mobile SMS can be plugged in separately.
    if user and user['email']:
        result = send_email(str(user['email']), "Your UPSC verification code", f"Your one-time verification code is {code}. It expires in {OTP_TTL_MINUTES} minutes.")
        if not result.delivered and os.getenv('APP_ENV','development') == 'production':
            conn.rollback(); conn.close(); raise HTTPException(503, "Email delivery is temporarily unavailable")
    conn.commit(); conn.close()
    out = {"sent": True, "expires_in_seconds": OTP_TTL_MINUTES*60, "channel": "email" if user and user['email'] else "none"}
    if os.getenv('APP_ENV','development') != 'production' and os.getenv('DEV_SHOW_OTP','true').lower() == 'true':
        out['development_otp'] = code
    return out


@app.post("/api/v1/auth/verify-otp")
def verify_otp(body: OTPVerifyBody) -> dict[str, Any]:
    conn = get_db(); identifier = body.identifier.strip()
    challenge = conn.execute("SELECT * FROM otp_challenges WHERE identifier=? AND purpose=? AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1", (identifier, body.purpose)).fetchone()
    if not challenge:
        conn.close(); raise HTTPException(400, "Verification code is invalid or expired")
    if utc_from_iso(challenge['expires_at']) < datetime.now(timezone.utc) or int(challenge['attempts']) >= 5:
        conn.close(); raise HTTPException(400, "Verification code is invalid or expired")
    if not hmac.compare_digest(hash_token(body.code), challenge['code_hash']):
        conn.execute("UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?", (challenge['id'],)); conn.commit(); conn.close(); raise HTTPException(400, "Verification code is invalid")
    ts = now_iso(); conn.execute("UPDATE otp_challenges SET consumed_at=? WHERE id=?", (ts, challenge['id']))
    if body.purpose == 'email_verify':
        user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (identifier,)).fetchone()
        if user:
            conn.execute("UPDATE users SET email_verified_at=?, updated_at=? WHERE id=?", (ts, ts, user['id']))
        conn.commit(); conn.close(); return {"verified": True, "purpose": body.purpose}
    user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) OR mobile=?", (identifier, identifier)).fetchone()
    if not user:
        conn.close(); raise HTTPException(400, "Account not found")
    raw_token = secrets.token_urlsafe(32); expires = (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()
    conn.execute("INSERT INTO password_reset_tokens VALUES (?,?,?,?,?,?)", (secrets.token_hex(12), user['id'], hash_token(raw_token), expires, None, ts))
    conn.commit(); conn.close()
    return {"verified": True, "purpose": body.purpose, "reset_token": raw_token, "expires_in_seconds": 900}


@app.post("/api/v1/auth/reset-password")
def reset_password(body: PasswordResetBody) -> dict[str, Any]:
    conn = get_db(); row = conn.execute("SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1", (hash_token(body.reset_token),)).fetchone()
    if not row or utc_from_iso(row['expires_at']) < datetime.now(timezone.utc):
        conn.close(); raise HTTPException(400, "Reset token is invalid or expired")
    ts = now_iso(); conn.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (hash_password(body.new_password), ts, row['user_id']))
    conn.execute("UPDATE password_reset_tokens SET used_at=? WHERE id=?", (ts, row['id']))
    conn.commit(); conn.close()
    return {"reset": True}


@app.post("/api/v1/auth/logout")
def logout(user: Any = Depends(get_current_user)) -> dict[str, bool]:
    return {"logged_out": True}


LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures: dict[str, list[float]] = {}


def _login_key(identifier: str) -> str:
    return identifier.strip().lower()


def _login_locked(key: str) -> bool:
    now = time.time()
    recent = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    if recent:
        _login_failures[key] = recent
    else:
        _login_failures.pop(key, None)
    return len(recent) >= LOGIN_MAX_FAILURES


@app.post("/api/v1/auth/login")
def login(body: LoginBody) -> dict[str, Any]:
    key = _login_key(body.identifier)
    if _login_locked(key):
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Please wait 15 minutes and try again.")
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?) OR mobile=?", (body.identifier, body.identifier)).fetchone()
    if not user or not verify_password(body.password, user["password_hash"]):
        conn.close()
        _login_failures.setdefault(key, []).append(time.time())
        raise HTTPException(status_code=401, detail="Invalid credentials")
    _login_failures.pop(key, None)
    if user["status"] == "pending_verification":
        conn.close()
        raise HTTPException(status_code=403, detail="Account verification is required before login")
    if user["status"] != "active":
        conn.close()
        raise HTTPException(status_code=403, detail="User account is not active")
    ts = now_iso()
    conn.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?", (ts, ts, user["id"]))
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    conn.close()
    return {"user": public_user(user), "access_token": make_token(user), "token_type": "bearer"}


@app.get("/api/v1/auth/me")
def me(user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return public_user(user)


@app.get("/api/v1/student/dashboard")
def student_dashboard(user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    attempted = conn.execute("SELECT COUNT(*) FROM attempts WHERE user_id=? AND status!='in_progress'", (user["id"],)).fetchone()[0]
    in_progress = conn.execute("SELECT COUNT(*) FROM attempts WHERE user_id=? AND status='in_progress'", (user["id"],)).fetchone()[0]
    mistakes = conn.execute("SELECT COUNT(*) FROM mistakes WHERE user_id=? AND resolved=0", (user["id"],)).fetchone()[0]
    bookmarks = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE user_id=?", (user["id"],)).fetchone()[0]
    upcoming = conn.execute("SELECT id,title,duration_minutes,total_questions,total_marks,scheduled_at FROM tests WHERE status='published' ORDER BY COALESCE(scheduled_at,'9999') LIMIT 5").fetchall()
    results = conn.execute("SELECT a.id,a.score,a.correct,a.incorrect,a.unattempted,a.submitted_at,t.title FROM attempts a JOIN tests t ON t.id=a.test_id WHERE a.user_id=? AND a.status!='in_progress' ORDER BY a.submitted_at DESC LIMIT 5", (user["id"],)).fetchall()
    conn.close()
    return {"user": public_user(user), "stats": {"tests_attempted": attempted, "in_progress": in_progress, "mistakes": mistakes, "bookmarks": bookmarks}, "upcoming_tests": [dict(x) for x in upcoming], "recent_results": [dict(x) for x in results]}


STAFF_ROLES = {"admin", "super_admin", "content_manager", "evaluator", "finance", "support"}
PLAN_REQUIRED_MESSAGE = "This test needs a higher plan. Choose a plan to unlock it."


def _active_plan_tier(conn: Any, user_id: str) -> int:
    """The highest tier among the student's currently active plans (0 if none). Plans are hierarchical:
    a tier-3 plan (e.g. Complete + CA) also unlocks everything a tier-1 or tier-2 plan unlocks."""
    row = conn.execute(
        "SELECT MAX(p.tier) AS tier FROM subscriptions s JOIN plans p ON p.id=s.plan_id "
        "WHERE s.user_id=? AND s.status='active' AND s.expires_at>?", (user_id, now_iso())).fetchone()
    return int(row["tier"] or 0)


def _can_access_test(test: Any, user: Any, plan_tier: int) -> bool:
    """Free tests are open to every logged-in student; other tests need a plan whose tier covers the
    test's required_tier (staff can always preview)."""
    return test["access_type"] == "free" or user["role"] in STAFF_ROLES or plan_tier >= int(test["required_tier"] or 1)


@app.get("/api/v1/student/tests")
def student_tests(user: Any = Depends(get_current_user)) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT t.*, s.name AS series_name FROM tests t LEFT JOIN test_series s ON s.id=t.series_id WHERE t.status='published' ORDER BY t.id").fetchall()
        tier = _active_plan_tier(conn, user["id"])
        return [{**dict(r), "has_access": _can_access_test(r, user, tier)} for r in rows]
    finally:
        conn.close()


@app.post("/api/v1/tests/{test_id}/start")
def start_test(test_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    test = conn.execute("SELECT * FROM tests WHERE id=? AND status='published'", (test_id,)).fetchone()
    if not test:
        conn.close(); raise HTTPException(404, "Test not found")
    existing = conn.execute("SELECT * FROM attempts WHERE user_id=? AND test_id=? AND status='in_progress' ORDER BY started_at DESC LIMIT 1", (user["id"], test_id)).fetchone()
    if not existing and test["scheduled_at"]:
        try:
            opens = datetime.fromisoformat(str(test["scheduled_at"]).replace("Z", "+00:00"))
            if opens.tzinfo is None:
                opens = opens.replace(tzinfo=timezone.utc)
        except ValueError:
            opens = None
        if opens and opens > datetime.now(timezone.utc):
            conn.close(); raise HTTPException(403, "This test opens on " + opens.strftime("%d %b %Y, %H:%M UTC"))
    if not existing and not _can_access_test(test, user, _active_plan_tier(conn, user["id"])):
        conn.close(); raise HTTPException(403, PLAN_REQUIRED_MESSAGE)
    if existing:
        attempt_id = existing["id"]
    else:
        attempt_id = secrets.token_hex(16)
        conn.execute("INSERT INTO attempts(id,user_id,test_id,started_at,status) VALUES (?,?,?,?,?)", (attempt_id, user["id"], test_id, now_iso(), "in_progress"))
        conn.commit()
    questions = conn.execute("SELECT tq.question_order,tq.marks,tq.negative_marks,q.id,q.stem,q.option_a,q.option_b,q.option_c,q.option_d FROM test_questions tq JOIN questions q ON q.id=tq.question_id WHERE tq.test_id=? ORDER BY tq.question_order", (test_id,)).fetchall()
    saved = conn.execute("SELECT question_id,selected_option,marked_for_review,time_spent_seconds FROM answers WHERE attempt_id=?", (attempt_id,)).fetchall()
    conn.close()
    return {"attempt_id": attempt_id, "test": dict(test), "questions": [dict(q) for q in questions], "saved_answers": [dict(a) for a in saved]}


@app.post("/api/v1/attempts/{attempt_id}/questions/{question_id}/answer")
def save_answer(attempt_id: str, question_id: str, body: AnswerBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    attempt = conn.execute("SELECT * FROM attempts WHERE id=? AND user_id=?", (attempt_id, user["id"])).fetchone()
    if not attempt or attempt["status"] != "in_progress":
        conn.close(); raise HTTPException(404, "Active attempt not found")
    question = conn.execute("SELECT correct_option FROM questions WHERE id=?", (question_id,)).fetchone()
    if not question:
        conn.close(); raise HTTPException(404, "Question not found")
    is_correct = None if body.selected_option is None else int(body.selected_option.upper() == question["correct_option"])
    conn.execute(
        "INSERT INTO answers(id,attempt_id,question_id,selected_option,is_correct,marked_for_review,time_spent_seconds) VALUES (?,?,?,?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET selected_option=excluded.selected_option,is_correct=excluded.is_correct,marked_for_review=excluded.marked_for_review,time_spent_seconds=excluded.time_spent_seconds",
        (secrets.token_hex(16), attempt_id, question_id, body.selected_option.upper() if body.selected_option else None, is_correct, int(body.marked_for_review), body.time_spent_seconds),
    )
    conn.commit(); conn.close()
    return {"saved": True, "is_correct": bool(is_correct) if is_correct is not None else None}


@app.post("/api/v1/attempts/{attempt_id}/submit")
def submit_attempt(attempt_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    attempt = conn.execute("SELECT * FROM attempts WHERE id=? AND user_id=?", (attempt_id, user["id"])).fetchone()
    if not attempt or attempt["status"] != "in_progress":
        conn.close(); raise HTTPException(404, "Active attempt not found")
    rows = conn.execute("SELECT a.is_correct,a.selected_option,q.correct_option,tq.marks,tq.negative_marks FROM answers a JOIN questions q ON q.id=a.question_id JOIN test_questions tq ON tq.question_id=q.id WHERE a.attempt_id=? AND tq.test_id=?", (attempt_id, attempt["test_id"])).fetchall()
    correct = sum(1 for r in rows if r["is_correct"] == 1)
    incorrect = sum(1 for r in rows if r["is_correct"] == 0)
    unattempted = conn.execute("SELECT COUNT(*) FROM test_questions tq WHERE tq.test_id=? AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.attempt_id=? AND a.question_id=tq.question_id AND a.selected_option IS NOT NULL)", (attempt["test_id"], attempt_id)).fetchone()[0]
    score = sum(float(r["marks"] if r["is_correct"] == 1 else -r["negative_marks"]) for r in rows)
    submitted = now_iso()
    conn.execute("UPDATE attempts SET status='evaluated',submitted_at=?,score=?,correct=?,incorrect=?,unattempted=? WHERE id=?", (submitted, score, correct, incorrect, unattempted, attempt_id))
    conn.commit()
    result = conn.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    conn.close()
    return {"attempt": dict(result)}


@app.get("/api/v1/tests/{test_id}/result")
def get_result(test_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    attempt = conn.execute("SELECT * FROM attempts WHERE test_id=? AND user_id=? AND status!='in_progress' ORDER BY submitted_at DESC LIMIT 1", (test_id, user["id"])).fetchone()
    if not attempt:
        conn.close(); raise HTTPException(404, "No completed attempt found")
    participants = conn.execute("SELECT COUNT(*) FROM attempts WHERE test_id=? AND status!='in_progress'", (test_id,)).fetchone()[0]
    better = conn.execute("SELECT COUNT(*) FROM attempts WHERE test_id=? AND status!='in_progress' AND score>?", (test_id, attempt["score"] or 0)).fetchone()[0]
    rank = better + 1
    percentile = round(100.0 * (participants - rank + 1) / participants, 1) if participants else 0
    rows = conn.execute("""SELECT q.id,q.stem,q.correct_option,q.explanation,q.subject_id,q.topic_id,q.option_a,q.option_b,q.option_c,q.option_d,a.selected_option,a.is_correct,a.marked_for_review FROM test_questions tq JOIN questions q ON q.id=tq.question_id LEFT JOIN answers a ON a.question_id=q.id AND a.attempt_id=? WHERE tq.test_id=? ORDER BY tq.question_order""", (attempt["id"], test_id)).fetchall()
    subject_rows = conn.execute("""
      SELECT COALESCE(s.name,'Other') subject, ROUND(100.0*SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN a.selected_option IS NOT NULL THEN 1 ELSE 0 END),0),1) accuracy, COUNT(*) questions
      FROM test_questions tq JOIN questions q ON q.id=tq.question_id LEFT JOIN subjects s ON s.id=q.subject_id LEFT JOIN answers a ON a.attempt_id=? AND a.question_id=q.id WHERE tq.test_id=? GROUP BY s.name
    """, (attempt["id"],test_id)).fetchall()
    topic_rows = conn.execute("""
      SELECT COALESCE(t.name,'Other') topic, ROUND(100.0*SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN a.selected_option IS NOT NULL THEN 1 ELSE 0 END),0),1) accuracy, COUNT(*) questions
      FROM test_questions tq JOIN questions q ON q.id=tq.question_id LEFT JOIN topics t ON t.id=q.topic_id LEFT JOIN answers a ON a.attempt_id=? AND a.question_id=q.id WHERE tq.test_id=? GROUP BY t.name
    """, (attempt["id"],test_id)).fetchall()
    conn.close()
    out_attempt=dict(attempt); out_attempt.update({"rank":rank,"percentile":percentile,"participants":participants})
    return {"attempt": out_attempt, "questions": [dict(r) for r in rows], "subjects":[dict(r) for r in subject_rows], "topics":[dict(r) for r in topic_rows]}


@app.get("/api/v1/student/questions")
def student_questions(
    subject: str | None = None,
    difficulty: str | None = None,
    year: int | None = None,
    search: str | None = Query(default=None, max_length=200),
    user: Any = Depends(get_current_user),
) -> list[dict[str, Any]]:
    conn = get_db()
    sql = "SELECT q.id,q.stem,q.option_a,q.option_b,q.option_c,q.option_d,q.difficulty,q.upsc_year,q.status,s.name AS subject_name,t.name AS topic_name FROM questions q LEFT JOIN subjects s ON s.id=q.subject_id LEFT JOIN topics t ON t.id=q.topic_id WHERE q.status='published'"
    args: list[Any] = []
    if subject: sql += " AND q.subject_id=?"; args.append(subject)
    if difficulty: sql += " AND q.difficulty=?"; args.append(difficulty)
    if year: sql += " AND q.upsc_year=?"; args.append(year)
    if search: sql += " AND q.stem LIKE ?"; args.append(f"%{search}%")
    sql += " ORDER BY q.updated_at DESC LIMIT 100"
    rows = conn.execute(sql, args).fetchall(); conn.close()
    return [dict(r) for r in rows]


@app.post("/api/v1/student/bookmarks/{content_type}/{content_id}")
def bookmark(content_type: str, content_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db(); conn.execute("INSERT OR IGNORE INTO bookmarks VALUES (?,?,?,?)", (user["id"], content_type, content_id, now_iso())); conn.commit(); conn.close(); return {"saved": True}


@app.delete("/api/v1/student/bookmarks/{content_type}/{content_id}")
def unbookmark(content_type: str, content_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db(); conn.execute("DELETE FROM bookmarks WHERE user_id=? AND content_type=? AND content_id=?", (user["id"], content_type, content_id)); conn.commit(); conn.close(); return {"saved": False}


@app.get("/api/v1/admin/students")
def admin_students(
    search: str | None = None,
    role: str | None = None,
    status_filter: str | None = None,
    admin: Any = Depends(require_admin),
) -> list[dict[str, Any]]:
    conn = get_db(); sql = "SELECT id,first_name,last_name,email,mobile,role,status,target_exam_year,preparation_stage,last_login_at,created_at FROM users WHERE 1=1"; args=[]
    if search: sql += " AND (first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR mobile LIKE ?)"; args += [f"%{search}%"]*4
    if role: sql += " AND role=?"; args.append(role)
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    sql += " ORDER BY created_at DESC LIMIT 200"; rows=conn.execute(sql,args).fetchall(); conn.close(); return [dict(r) for r in rows]


@app.get("/api/v1/admin/questions")
def admin_questions(search: str | None = None, subject: str | None = None, status_filter: str | None = None, difficulty: str | None = None, limit: int = Query(default=200, ge=1, le=500), admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT q.*,s.name AS subject_name,t.name AS topic_name FROM questions q LEFT JOIN subjects s ON s.id=q.subject_id LEFT JOIN topics t ON t.id=q.topic_id WHERE 1=1"; args=[]
    if search: sql += " AND q.stem LIKE ?"; args.append(f"%{search}%")
    if subject: sql += " AND q.subject_id=?"; args.append(subject)
    if status_filter: sql += " AND q.status=?"; args.append(status_filter)
    if difficulty: sql += " AND q.difficulty=?"; args.append(difficulty.lower())
    rows=conn.execute(sql+" ORDER BY q.updated_at DESC LIMIT ?",args+[limit]).fetchall(); conn.close(); return [dict(r) for r in rows]



@app.get("/api/v1/tests/{test_id}")
def get_test(test_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    try:
        test = conn.execute("SELECT t.*, s.name AS series_name FROM tests t LEFT JOIN test_series s ON s.id=t.series_id WHERE t.id=? AND t.status='published'", (test_id,)).fetchone()
        if not test: raise HTTPException(404, "Test not found")
        return {**dict(test), "has_access": _can_access_test(test, user, _active_plan_tier(conn, user["id"]))}
    finally:
        conn.close()

@app.get("/api/v1/student/performance")
def student_performance(user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn = get_db()
    attempts = conn.execute("SELECT a.score,a.correct,a.incorrect,a.unattempted,a.submitted_at,t.title,t.test_type FROM attempts a JOIN tests t ON t.id=a.test_id WHERE a.user_id=? AND a.status!='in_progress' ORDER BY a.submitted_at ASC", (user["id"],)).fetchall()
    subjects = conn.execute("""
        SELECT s.name subject, ROUND(100.0 * SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN a.selected_option IS NOT NULL THEN 1 ELSE 0 END),0),1) accuracy, COUNT(*) questions
        FROM answers a JOIN questions q ON q.id=a.question_id LEFT JOIN subjects s ON s.id=q.subject_id
        JOIN attempts at ON at.id=a.attempt_id WHERE at.user_id=? AND at.status!='in_progress' GROUP BY s.name ORDER BY accuracy DESC
    """, (user["id"],)).fetchall()
    topics = conn.execute("""
        SELECT t.name topic, ROUND(100.0 * SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN a.selected_option IS NOT NULL THEN 1 ELSE 0 END),0),1) accuracy, COUNT(*) questions
        FROM answers a JOIN questions q ON q.id=a.question_id LEFT JOIN topics t ON t.id=q.topic_id
        JOIN attempts at ON at.id=a.attempt_id WHERE at.user_id=? AND at.status!='in_progress' GROUP BY t.name HAVING t.name IS NOT NULL ORDER BY accuracy ASC LIMIT 20
    """, (user["id"],)).fetchall()
    conn.close()
    completed=[dict(r) for r in attempts]
    scores=[float(r["score"] or 0) for r in attempts]
    return {"tests_attempted":len(completed),"average_score":round(sum(scores)/len(scores),1) if scores else 0,"trend":[{"label":r["title"],"score":r["score"]} for r in attempts[-12:]],"subjects":[dict(r) for r in subjects],"topics":[dict(r) for r in topics]}

@app.get("/api/v1/student/bookmarks")
def get_bookmarks(user: Any = Depends(get_current_user)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT * FROM bookmarks WHERE user_id=? ORDER BY created_at DESC",(user["id"],)).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/student/mistakes")
def get_mistakes(user: Any = Depends(get_current_user)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("""SELECT m.*,q.stem,q.explanation,q.correct_option,s.name subject_name,t.name topic_name FROM mistakes m JOIN questions q ON q.id=m.question_id LEFT JOIN subjects s ON s.id=q.subject_id LEFT JOIN topics t ON t.id=q.topic_id WHERE m.user_id=? ORDER BY m.created_at DESC""",(user["id"],)).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/student/notifications")
def get_notifications(user: Any = Depends(get_current_user)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",(user["id"],)).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/student/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",(notification_id,user["id"])); conn.commit(); conn.close()
    if cur.rowcount==0: raise HTTPException(404,"Notification not found")
    return {"read":True}

@app.get("/api/v1/student/subscription")
def student_subscription(user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT s.*,p.name plan_name,p.slug,p.price_paise,p.validity_days FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE s.user_id=? ORDER BY s.expires_at DESC LIMIT 1",(user["id"],)).fetchone(); conn.close()
    return dict(row) if row else {"status":"none"}

@app.put("/api/v1/student/profile")
def update_profile(body: ProfileUpdateBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn=get_db(); updates=[]; args=[]
    for field in ("first_name","last_name","mobile","target_exam_year","preparation_stage"):
        value=getattr(body,field)
        if value is not None:
            updates.append(f"{field}=?"); args.append(str(value).strip() if isinstance(value,str) else value)
    if updates:
        updates.append("updated_at=?"); args.append(now_iso()); args.append(user["id"])
        try: conn.execute("UPDATE users SET "+",".join(updates)+" WHERE id=?",args); conn.commit()
        except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Mobile number is already in use")
    refreshed=conn.execute("SELECT * FROM users WHERE id=?",(user["id"],)).fetchone(); conn.close(); return public_user(refreshed)

@app.get("/api/v1/public/current-affairs")
def public_current_affairs(search: str|None=None, category: str|None=None, gs: str|None=None, month: str|None=None, limit: int=50) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM current_affairs WHERE status='published'"; args=[]
    if search: sql += " AND (title LIKE ? OR summary LIKE ? OR category LIKE ? OR key_points LIKE ?)"; args += [f"%{search}%"]*4
    if category and category!='all': sql += " AND category=?"; args.append(category)
    if gs and gs!='all': sql += " AND gs=?"; args.append(gs)
    if month and month!='all': sql += " AND month=?"; args.append(month)
    sql += " ORDER BY published_at DESC LIMIT ?"; args.append(min(max(limit,1),100)); rows=conn.execute(sql,args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/public/current-affairs/{article_id}")
def public_current_affairs_detail(article_id: str) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT * FROM current_affairs WHERE id=? AND status='published'",(article_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Article not found")
    return dict(row)

@app.get("/api/v1/public/study-material")
def public_study_material(search: str|None=None, subject: str|None=None, format: str|None=None, access_type: str|None=None, limit: int=50) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM study_material WHERE status='published'"; args=[]
    if search: sql += " AND (title LIKE ? OR summary LIKE ? OR topic LIKE ? OR tags LIKE ?)"; args += [f"%{search}%"]*4
    if subject and subject!='all': sql += " AND subject=?"; args.append(subject)
    if format and format!='all': sql += " AND format=?"; args.append(format)
    if access_type and access_type!='all': sql += " AND access_type=?"; args.append(access_type)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(min(max(limit,1),100)); rows=conn.execute(sql,args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/public/study-material/{material_id}")
def public_study_material_detail(material_id: str) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT * FROM study_material WHERE id=? AND status='published'",(material_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Study material not found")
    return dict(row)

@app.get("/api/v1/public/blog")
def public_blog(search: str|None=None, category: str|None=None, limit: int=50) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM blog_posts WHERE status='published'"; args=[]
    if search: sql += " AND (title LIKE ? OR excerpt LIKE ? OR category LIKE ? OR content LIKE ?)"; args += [f"%{search}%"]*4
    if category and category!='all': sql += " AND category=?"; args.append(category)
    sql += " ORDER BY published_at DESC LIMIT ?"; args.append(min(max(limit,1),100)); rows=conn.execute(sql,args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/public/blog/{slug}")
def public_blog_detail(slug: str) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT * FROM blog_posts WHERE slug=? AND status='published'",(slug,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Blog post not found")
    return dict(row)

@app.post("/api/v1/support/enquiries")
def create_support(body: SupportBody) -> dict[str, Any]:
    conn=get_db(); enquiry_id=secrets.token_hex(12); conn.execute("INSERT INTO support_enquiries VALUES (?,?,?,?,?,?,?,?)",(enquiry_id,None,body.name.strip(),str(body.email) if body.email else None,body.subject.strip(),body.message.strip(),"open",now_iso())); conn.commit(); conn.close(); return {"id":enquiry_id,"status":"open"}

@app.get("/api/v1/admin/current-affairs")
def admin_current_affairs(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM current_affairs WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR category LIKE ? OR summary LIKE ?)"; args += [f"%{search}%"]*3
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY published_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/current-affairs")
def admin_create_current_affair(body: CurrentAffairBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); aid=secrets.token_hex(12); ts=body.published_at or now_iso();
    conn.execute("INSERT INTO current_affairs(id,title,category,gs,month,published_at,minutes,summary,key_points,status,author,source,featured,mcq_link,revision,seo,content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid,body.title,body.category,body.gs,body.month,ts,body.minutes,body.summary,body.key_points,body.status,body.author,body.source,int(body.featured),body.mcq_link,int(body.revision),int(body.seo),body.content))
    conn.commit(); row=conn.execute("SELECT * FROM current_affairs WHERE id=?",(aid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/current-affairs/{article_id}")
def admin_update_current_affair(article_id: str, body: CurrentAffairBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE current_affairs SET title=?,category=?,gs=?,month=?,published_at=?,minutes=?,summary=?,key_points=?,status=?,author=?,source=?,featured=?,mcq_link=?,revision=?,seo=?,content=? WHERE id=?", (body.title,body.category,body.gs,body.month,body.published_at or now_iso(),body.minutes,body.summary,body.key_points,body.status,body.author,body.source,int(body.featured),body.mcq_link,int(body.revision),int(body.seo),body.content,article_id)); conn.commit(); row=conn.execute("SELECT * FROM current_affairs WHERE id=?",(article_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Current affairs article not found")
    return dict(row)

@app.delete("/api/v1/admin/current-affairs/{article_id}")
def admin_delete_current_affair(article_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM current_affairs WHERE id=?",(article_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Current affairs article not found")
    return {"deleted":True}

@app.get("/api/v1/admin/study-material")
def admin_study_material(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM study_material WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR subject LIKE ? OR topic LIKE ? OR tags LIKE ?)"; args += [f"%{search}%"]*4
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY id DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/study-material")
def admin_create_study_material(body: StudyMaterialBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); mid=secrets.token_hex(12);
    conn.execute("INSERT INTO study_material(id,title,subject,topic,format,access_type,pages,summary,progress,status,owner,tags,source,featured,track,practice,seo,file_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (mid,body.title,body.subject,body.topic,body.format,body.access_type,body.pages,body.summary,0,body.status,body.owner,body.tags,body.source,int(body.featured),int(body.track),body.practice,int(body.seo),body.file_url)); conn.commit(); row=conn.execute("SELECT * FROM study_material WHERE id=?",(mid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/study-material/{material_id}")
def admin_update_study_material(material_id: str, body: StudyMaterialBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE study_material SET title=?,subject=?,topic=?,format=?,access_type=?,pages=?,summary=?,status=?,owner=?,tags=?,source=?,featured=?,track=?,practice=?,seo=?,file_url=? WHERE id=?", (body.title,body.subject,body.topic,body.format,body.access_type,body.pages,body.summary,body.status,body.owner,body.tags,body.source,int(body.featured),int(body.track),body.practice,int(body.seo),body.file_url,material_id)); conn.commit(); row=conn.execute("SELECT * FROM study_material WHERE id=?",(material_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Study material not found")
    return dict(row)

@app.delete("/api/v1/admin/study-material/{material_id}")
def admin_delete_study_material(material_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM study_material WHERE id=?",(material_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Study material not found")
    return {"deleted":True}

@app.get("/api/v1/admin/blog")
def admin_blog(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM blog_posts WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR excerpt LIKE ? OR category LIKE ? OR author LIKE ?)"; args += [f"%{search}%"]*4
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY published_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/blog")
def admin_create_blog(body: BlogPostBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); bid=secrets.token_hex(12);
    try:
        conn.execute("INSERT INTO blog_posts(id,title,slug,excerpt,category,author,status,published_at,format,image,seo_title,meta_description,featured,newsletter,related_tests,revision,allow_comments,content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (bid,body.title,body.slug,body.excerpt,body.category,body.author,body.status,body.published_at,body.format,body.image,body.seo_title,body.meta_description,int(body.featured),int(body.newsletter),body.related_tests,int(body.revision),int(body.allow_comments),body.content)); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Blog slug already exists")
    row=conn.execute("SELECT * FROM blog_posts WHERE id=?",(bid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/blog/{post_id}")
def admin_update_blog(post_id: str, body: BlogPostBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db();
    try:
        conn.execute("UPDATE blog_posts SET title=?,slug=?,excerpt=?,category=?,author=?,status=?,published_at=?,format=?,image=?,seo_title=?,meta_description=?,featured=?,newsletter=?,related_tests=?,revision=?,allow_comments=?,content=? WHERE id=?", (body.title,body.slug,body.excerpt,body.category,body.author,body.status,body.published_at,body.format,body.image,body.seo_title,body.meta_description,int(body.featured),int(body.newsletter),body.related_tests,int(body.revision),int(body.allow_comments),body.content,post_id)); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Blog slug already exists")
    row=conn.execute("SELECT * FROM blog_posts WHERE id=?",(post_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Blog post not found")
    return dict(row)

@app.delete("/api/v1/admin/blog/{post_id}")
def admin_delete_blog(post_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM blog_posts WHERE id=?",(post_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Blog post not found")
    return {"deleted":True}

@app.get("/api/v1/admin/support/enquiries")
def admin_support_enquiries(status_filter: str|None=None, search: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM support_enquiries WHERE 1=1"; args=[]
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    if search: sql += " AND (name LIKE ? OR email LIKE ? OR subject LIKE ? OR message LIKE ?)"; args += [f"%{search}%"]*4
    rows=conn.execute(sql+" ORDER BY created_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.put("/api/v1/admin/support/enquiries/{enquiry_id}")
def admin_update_support_enquiry(enquiry_id: str, body: dict[str, Any], admin: Any = Depends(require_admin)) -> dict[str, Any]:
    new_status = str(body.get("status") or "open")
    if new_status not in {"open","in_progress","resolved","closed"}:
        raise HTTPException(400,"Invalid enquiry status")
    conn=get_db(); conn.execute("UPDATE support_enquiries SET status=? WHERE id=?",(new_status,enquiry_id)); conn.commit(); row=conn.execute("SELECT * FROM support_enquiries WHERE id=?",(enquiry_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Enquiry not found")
    return dict(row)

@app.get("/api/v1/admin/current-affairs")
def admin_current_affairs(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM current_affairs WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR category LIKE ? OR summary LIKE ?)"; args += [f"%{search}%"]*3
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY published_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/current-affairs")
def admin_create_current_affair(body: CurrentAffairBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); aid=secrets.token_hex(12); ts=body.published_at or now_iso();
    conn.execute("INSERT INTO current_affairs(id,title,category,gs,month,published_at,minutes,summary,key_points,status,author,source,featured,mcq_link,revision,seo,content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (aid,body.title,body.category,body.gs,body.month,ts,body.minutes,body.summary,body.key_points,body.status,body.author,body.source,int(body.featured),body.mcq_link,int(body.revision),int(body.seo),body.content))
    conn.commit(); row=conn.execute("SELECT * FROM current_affairs WHERE id=?",(aid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/current-affairs/{article_id}")
def admin_update_current_affair(article_id: str, body: CurrentAffairBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE current_affairs SET title=?,category=?,gs=?,month=?,published_at=?,minutes=?,summary=?,key_points=?,status=?,author=?,source=?,featured=?,mcq_link=?,revision=?,seo=?,content=? WHERE id=?", (body.title,body.category,body.gs,body.month,body.published_at or now_iso(),body.minutes,body.summary,body.key_points,body.status,body.author,body.source,int(body.featured),body.mcq_link,int(body.revision),int(body.seo),body.content,article_id)); conn.commit(); row=conn.execute("SELECT * FROM current_affairs WHERE id=?",(article_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Current affairs article not found")
    return dict(row)

@app.delete("/api/v1/admin/current-affairs/{article_id}")
def admin_delete_current_affair(article_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM current_affairs WHERE id=?",(article_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Current affairs article not found")
    return {"deleted":True}

@app.get("/api/v1/admin/study-material")
def admin_study_material(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM study_material WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR subject LIKE ? OR topic LIKE ? OR tags LIKE ?)"; args += [f"%{search}%"]*4
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY id DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/study-material")
def admin_create_study_material(body: StudyMaterialBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); mid=secrets.token_hex(12);
    conn.execute("INSERT INTO study_material(id,title,subject,topic,format,access_type,pages,summary,progress,status,owner,tags,source,featured,track,practice,seo,file_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (mid,body.title,body.subject,body.topic,body.format,body.access_type,body.pages,body.summary,0,body.status,body.owner,body.tags,body.source,int(body.featured),int(body.track),body.practice,int(body.seo),body.file_url)); conn.commit(); row=conn.execute("SELECT * FROM study_material WHERE id=?",(mid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/study-material/{material_id}")
def admin_update_study_material(material_id: str, body: StudyMaterialBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE study_material SET title=?,subject=?,topic=?,format=?,access_type=?,pages=?,summary=?,status=?,owner=?,tags=?,source=?,featured=?,track=?,practice=?,seo=?,file_url=? WHERE id=?", (body.title,body.subject,body.topic,body.format,body.access_type,body.pages,body.summary,body.status,body.owner,body.tags,body.source,int(body.featured),int(body.track),body.practice,int(body.seo),body.file_url,material_id)); conn.commit(); row=conn.execute("SELECT * FROM study_material WHERE id=?",(material_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Study material not found")
    return dict(row)

@app.delete("/api/v1/admin/study-material/{material_id}")
def admin_delete_study_material(material_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM study_material WHERE id=?",(material_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Study material not found")
    return {"deleted":True}

@app.get("/api/v1/admin/blog")
def admin_blog(search: str|None=None, status_filter: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM blog_posts WHERE 1=1"; args=[]
    if search: sql += " AND (title LIKE ? OR excerpt LIKE ? OR category LIKE ? OR author LIKE ?)"; args += [f"%{search}%"]*4
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    rows=conn.execute(sql+" ORDER BY published_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/blog")
def admin_create_blog(body: BlogPostBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); bid=secrets.token_hex(12);
    try:
        conn.execute("INSERT INTO blog_posts(id,title,slug,excerpt,category,author,status,published_at,format,image,seo_title,meta_description,featured,newsletter,related_tests,revision,allow_comments,content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (bid,body.title,body.slug,body.excerpt,body.category,body.author,body.status,body.published_at,body.format,body.image,body.seo_title,body.meta_description,int(body.featured),int(body.newsletter),body.related_tests,int(body.revision),int(body.allow_comments),body.content)); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Blog slug already exists")
    row=conn.execute("SELECT * FROM blog_posts WHERE id=?",(bid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/blog/{post_id}")
def admin_update_blog(post_id: str, body: BlogPostBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db();
    try:
        conn.execute("UPDATE blog_posts SET title=?,slug=?,excerpt=?,category=?,author=?,status=?,published_at=?,format=?,image=?,seo_title=?,meta_description=?,featured=?,newsletter=?,related_tests=?,revision=?,allow_comments=?,content=? WHERE id=?", (body.title,body.slug,body.excerpt,body.category,body.author,body.status,body.published_at,body.format,body.image,body.seo_title,body.meta_description,int(body.featured),int(body.newsletter),body.related_tests,int(body.revision),int(body.allow_comments),body.content,post_id)); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Blog slug already exists")
    row=conn.execute("SELECT * FROM blog_posts WHERE id=?",(post_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Blog post not found")
    return dict(row)

@app.delete("/api/v1/admin/blog/{post_id}")
def admin_delete_blog(post_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("DELETE FROM blog_posts WHERE id=?",(post_id,)); conn.commit(); conn.close();
    if cur.rowcount==0: raise HTTPException(404,"Blog post not found")
    return {"deleted":True}

@app.get("/api/v1/admin/support/enquiries")
def admin_support_enquiries(status_filter: str|None=None, search: str|None=None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="SELECT * FROM support_enquiries WHERE 1=1"; args=[]
    if status_filter: sql += " AND status=?"; args.append(status_filter)
    if search: sql += " AND (name LIKE ? OR email LIKE ? OR subject LIKE ? OR message LIKE ?)"; args += [f"%{search}%"]*4
    rows=conn.execute(sql+" ORDER BY created_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.put("/api/v1/admin/support/enquiries/{enquiry_id}")
def admin_update_support_enquiry(enquiry_id: str, body: dict[str, Any], admin: Any = Depends(require_admin)) -> dict[str, Any]:
    new_status = str(body.get("status") or "open")
    if new_status not in {"open","in_progress","resolved","closed"}:
        raise HTTPException(400,"Invalid enquiry status")
    conn=get_db(); conn.execute("UPDATE support_enquiries SET status=? WHERE id=?",(new_status,enquiry_id)); conn.commit(); row=conn.execute("SELECT * FROM support_enquiries WHERE id=?",(enquiry_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Enquiry not found")
    return dict(row)

@app.post("/api/v1/admin/notifications")
def admin_create_notification(body: NotificationCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); users=conn.execute("SELECT * FROM users WHERE role='student' AND status='active'").fetchall(); created=[]; delivered=0; failed=0; ts=now_iso()
    for u in users:
        nid=secrets.token_hex(12)
        conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)",(nid,u["id"],body.title,body.body,body.type,0,body.href or "student-dashboard.html",ts))
        created.append(nid)
        if body.channel in {'email','both'} and u["email"]:
            result=send_email(str(u["email"]),body.title,body.body)
            status_value='delivered' if result.delivered else 'failed'
            if result.delivered: delivered += 1
            else: failed += 1
            conn.execute("INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?,?)",(secrets.token_hex(12),nid,'email',result.provider,status_value,None if result.delivered else result.message,ts if result.delivered else None,ts))
        if body.channel in {'in_app','both'}:
            conn.execute("INSERT INTO notification_deliveries VALUES (?,?,?,?,?,?,?,?)",(secrets.token_hex(12),nid,'in_app','platform','delivered',None,ts,ts))
            delivered += 1
    conn.commit(); conn.close(); return {"created":len(created),"delivered":delivered,"failed":failed,"channel":body.channel}


@app.get("/api/v1/admin/dashboard")
def admin_dashboard(admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db()
    students=conn.execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
    active=conn.execute("SELECT COUNT(*) FROM users WHERE role='student' AND status='active'").fetchone()[0]
    questions=conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
    tests=conn.execute("SELECT COUNT(*) FROM tests").fetchone()[0]
    attempts=conn.execute("SELECT COUNT(*) FROM attempts WHERE status!='in_progress'").fetchone()[0]
    series=conn.execute("SELECT COUNT(*) FROM test_series").fetchone()[0]
    conn.close(); return {"students":students,"active_students":active,"questions":questions,"tests":tests,"attempts":attempts,"series":series}

@app.get("/api/v1/admin/students/{student_id}")
def admin_student(student_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT id,first_name,last_name,email,mobile,role,status,target_exam_year,preparation_stage,created_at,last_login_at FROM users WHERE id=?",(student_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Student not found")
    return dict(row)

@app.put("/api/v1/admin/students/{student_id}")
def admin_update_student(student_id: str, body: StudentUpdateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); updates=[]; args=[]
    for f in ("status","role","target_exam_year","preparation_stage"):
        v=getattr(body,f)
        if v is not None: updates.append(f"{f}=?"); args.append(v)
    if updates: updates.append("updated_at=?"); args.append(now_iso()); args.append(student_id); conn.execute("UPDATE users SET "+",".join(updates)+" WHERE id=?",args); conn.commit()
    row=conn.execute("SELECT id,first_name,last_name,email,mobile,role,status,target_exam_year,preparation_stage,created_at,last_login_at FROM users WHERE id=?",(student_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Student not found")
    return dict(row)

@app.post("/api/v1/admin/questions")
def admin_create_question(body: QuestionCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); qid=secrets.token_hex(12); ts=now_iso(); conn.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(qid,body.subject_id,body.topic_id,body.stem,body.option_a,body.option_b,body.option_c,body.option_d,body.correct_option.upper(),body.explanation,body.difficulty.lower(),body.upsc_year,body.status,body.source,ts,ts)); conn.commit(); row=conn.execute("SELECT * FROM questions WHERE id=?",(qid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/questions/{question_id}")
def admin_update_question(question_id: str, body: QuestionCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); conn.execute("UPDATE questions SET subject_id=?,topic_id=?,stem=?,option_a=?,option_b=?,option_c=?,option_d=?,correct_option=?,explanation=?,difficulty=?,upsc_year=?,status=?,source=?,updated_at=? WHERE id=?",(body.subject_id,body.topic_id,body.stem,body.option_a,body.option_b,body.option_c,body.option_d,body.correct_option.upper(),body.explanation,body.difficulty.lower(),body.upsc_year,body.status,body.source,now_iso(),question_id)); conn.commit(); row=conn.execute("SELECT * FROM questions WHERE id=?",(question_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Question not found")
    return dict(row)

@app.delete("/api/v1/admin/questions/{question_id}")
def admin_delete_question(question_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); used=conn.execute("SELECT COUNT(*) FROM test_questions WHERE question_id=?",(question_id,)).fetchone()[0]
    if used: conn.close(); raise HTTPException(409,"Question is already used by a test")
    cur=conn.execute("DELETE FROM questions WHERE id=?",(question_id,)); conn.commit(); conn.close()
    if cur.rowcount==0: raise HTTPException(404,"Question not found")
    return {"deleted":True}


_DIFFICULTY_ALIASES = {"easy": "easy", "moderate": "moderate", "medium": "moderate", "difficult": "difficult", "hard": "difficult"}


@app.post("/api/v1/admin/questions/import")
async def admin_import_questions(
    file: UploadFile = File(...),
    dry_run: bool = Query(default=True),
    subject_id: str | None = Query(default=None),
    topic: str | None = Query(default=None, max_length=120),
    difficulty: str = Query(default="moderate"),
    status_value: str = Query(default="draft", alias="status"),
    source: str | None = Query(default=None, max_length=200),
    year: int | None = Query(default=None, ge=1990, le=2100),
    admin: Any = Depends(require_admin),
) -> dict[str, Any]:
    """Bulk-create questions from a Word (.docx) or CSV file.

    dry_run=true only parses and validates (nothing is written); dry_run=false imports every valid
    question and skips the flagged ones, reporting why.
    """
    filename = file.filename or "upload"
    lower = filename.lower()
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit")
    if lower.endswith(".docx"):
        parser, fmt = parse_docx, "docx"
    elif lower.endswith(".csv"):
        parser, fmt = parse_csv, "csv"
    elif lower.endswith(".doc"):
        raise HTTPException(400, "Old .doc files are not supported. In Word use File > Save As > Word Document (.docx) and upload that.")
    elif lower.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Excel files are not supported. In Excel use File > Save As > CSV (.csv) and upload that.")
    else:
        raise HTTPException(400, "Upload a Word (.docx) or CSV (.csv) file.")

    default_difficulty = _DIFFICULTY_ALIASES.get(difficulty.strip().lower())
    if not default_difficulty:
        raise HTTPException(400, "difficulty must be easy, moderate or difficult")
    if status_value not in {"draft", "published"}:
        raise HTTPException(400, "status must be draft or published")
    try:
        parsed = parser(data)
    except ImportFileError as exc:
        raise HTTPException(400, str(exc))
    if len(parsed) > MAX_QUESTIONS_PER_IMPORT:
        raise HTTPException(400, f"This file has {len(parsed)} questions; the limit is {MAX_QUESTIONS_PER_IMPORT} per import. Split it into smaller files.")

    conn = get_db()
    try:
        subject_rows = conn.execute("SELECT id,name,slug FROM subjects").fetchall()
        subject_lookup: dict[str, str] = {}
        for r in subject_rows:
            for key in (r["id"], r["name"], r["slug"]):
                subject_lookup[str(key).strip().lower()] = r["id"]
        default_subject = None
        if subject_id:
            default_subject = subject_lookup.get(subject_id.strip().lower())
            if not default_subject:
                raise HTTPException(400, "Unknown subject")
        elif fmt == "docx":
            raise HTTPException(400, "Choose a subject for the questions in this file.")

        seen = {normalise_stem(r["stem"]): "an existing question" for r in conn.execute("SELECT stem FROM questions").fetchall()}
        good: list[tuple[Any, str, str | None, str]] = []   # (question, subject, topic name, difficulty)
        flagged: list[dict[str, Any]] = []
        for q in parsed:
            problems = list(q.errors)
            q_subject = default_subject
            if q.subject:
                q_subject = subject_lookup.get(q.subject.strip().lower())
                if not q_subject:
                    problems.append(f"unknown subject '{q.subject}'")
            elif not q_subject:
                problems.append("subject is missing")
            q_difficulty = default_difficulty
            if q.difficulty:
                q_difficulty = _DIFFICULTY_ALIASES.get(q.difficulty.strip().lower())
                if not q_difficulty:
                    problems.append(f"difficulty '{q.difficulty}' is not easy, moderate or difficult")
            if q.year is not None and not 1990 <= q.year <= 2100:
                problems.append(f"year {q.year} is out of range")
            key = normalise_stem(q.stem)
            if key and key in seen:
                problems.append(f"duplicate of {seen[key]}")
            if problems:
                flagged.append({"number": q.number, "problems": problems})
                continue
            seen[key] = f"question {q.number} in this file"
            good.append((q, q_subject, (q.topic or topic or "").strip() or None, q_difficulty))

        created = 0
        if not dry_run and good:
            try:
                topic_ids: dict[tuple[str, str], str] = {}
                batch_start = datetime.now(timezone.utc)
                for order, (q, q_subject, topic_name, q_difficulty) in enumerate(good):
                    # Lists are sorted newest-first; stepping back a microsecond per question keeps the file's order.
                    ts = (batch_start - timedelta(microseconds=order)).isoformat()
                    topic_id = None
                    if topic_name:
                        tkey = (q_subject, slugify(topic_name))
                        if tkey not in topic_ids:
                            row = conn.execute("SELECT id FROM topics WHERE subject_id=? AND slug=?", tkey).fetchone()
                            if row:
                                topic_ids[tkey] = row["id"]
                            else:
                                new_id = f"{q_subject}-{tkey[1]}"[:80]
                                if conn.execute("SELECT 1 FROM topics WHERE id=?", (new_id,)).fetchone():
                                    new_id = f"{new_id[:70]}-{secrets.token_hex(3)}"
                                conn.execute("INSERT INTO topics VALUES (?,?,?,?)", (new_id, q_subject, topic_name, tkey[1]))
                                topic_ids[tkey] = new_id
                        topic_id = topic_ids[tkey]
                    conn.execute(
                        "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (secrets.token_hex(12), q_subject, topic_id, q.stem, q.options["A"], q.options["B"], q.options["C"], q.options["D"],
                         q.answer, q.explanation or None, q_difficulty, q.year or year, status_value, q.source or source or None, ts, ts),
                    )
                    created += 1
                conn.commit()
            except DB_ERROR:
                conn.rollback()
                logger.exception("question import failed")
                raise HTTPException(500, "Import failed and nothing was saved. Please try again.")
    finally:
        conn.close()

    return {
        "dry_run": dry_run,
        "filename": filename,
        "format": fmt,
        "total": len(parsed),
        "valid": len(good),
        "flagged": len(flagged),
        "created": created,
        "errors": flagged[:50],
        "preview": [
            {"number": q.number, "stem": q.stem, "options": q.options, "answer": q.answer}
            for q, *_ in good[:3]
        ],
    }

_TEST_STATUSES = {"draft", "published", "archived"}
_TEST_ACCESS = {"premium", "free", "included"}
MAX_QUESTIONS_PER_TEST = 300


def _norm_release(value: str | None) -> str | None:
    """Store release times as UTC ISO strings so they can be compared reliably."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "Release date/time is not valid")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _unique_test_slug(conn: Any, base: str, exclude_id: str | None = None) -> str:
    base = slugify(base) or "test"
    candidate, n = base, 1
    while True:
        row = conn.execute("SELECT id FROM tests WHERE slug=?", (candidate,)).fetchone()
        if not row or row["id"] == exclude_id:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def _prepare_test(conn: Any, body: "TestCreateBody", exclude_id: str | None = None) -> tuple[list[str], str, str | None]:
    if body.status not in _TEST_STATUSES:
        raise HTTPException(400, "status must be draft, published or archived")
    if body.access_type not in _TEST_ACCESS:
        raise HTTPException(400, "access must be premium, free or included")
    if body.access_type != "free":
        known_tiers = {r["tier"] for r in conn.execute("SELECT DISTINCT tier FROM plans WHERE is_public=1").fetchall()}
        if known_tiers and body.required_tier not in known_tiers:
            raise HTTPException(400, f"required_tier must match a real plan tier ({sorted(known_tiers)})")
    if body.series_id and not conn.execute("SELECT 1 FROM test_series WHERE id=?", (body.series_id,)).fetchone():
        raise HTTPException(400, "Unknown test series")
    qids = list(dict.fromkeys(body.question_ids))          # drop duplicates, keep order
    if len(qids) > MAX_QUESTIONS_PER_TEST:
        raise HTTPException(400, f"A test can have at most {MAX_QUESTIONS_PER_TEST} questions")
    if qids:
        marks = ",".join("?" for _ in qids)
        found = {r["id"] for r in conn.execute(f"SELECT id FROM questions WHERE id IN ({marks})", qids).fetchall()}
        if len(found) != len(qids):
            raise HTTPException(400, f"{len(qids) - len(found)} selected question(s) no longer exist. Reopen the question picker.")
    if body.status == "published" and not qids:
        raise HTTPException(400, "Add at least one question before publishing this test")
    return qids, _unique_test_slug(conn, body.slug, exclude_id), _norm_release(body.scheduled_at)


def _test_detail(conn: Any, test_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT t.*,s.name series_name,(SELECT COUNT(*) FROM attempts a WHERE a.test_id=t.id) attempt_count FROM tests t LEFT JOIN test_series s ON s.id=t.series_id WHERE t.id=?", (test_id,)).fetchone()
    if not row:
        return None
    qs = conn.execute("SELECT tq.question_order,tq.marks,tq.negative_marks,q.id,q.stem,q.difficulty,q.status,sub.name subject_name,tp.name topic_name FROM test_questions tq JOIN questions q ON q.id=tq.question_id LEFT JOIN subjects sub ON sub.id=q.subject_id LEFT JOIN topics tp ON tp.id=q.topic_id WHERE tq.test_id=? ORDER BY tq.question_order", (test_id,)).fetchall()
    return {**dict(row), "questions": [dict(q) for q in qs]}


@app.get("/api/v1/admin/tests")
def admin_tests(admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT t.*,s.name series_name,(SELECT COUNT(*) FROM test_questions tq WHERE tq.test_id=t.id) question_count,(SELECT COUNT(*) FROM attempts a WHERE a.test_id=t.id) attempt_count FROM tests t LEFT JOIN test_series s ON s.id=t.series_id ORDER BY t.title").fetchall(); conn.close(); return [dict(r) for r in rows]


@app.get("/api/v1/admin/tests/{test_id}")
def admin_get_test(test_id: str, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn = get_db()
    try:
        detail = _test_detail(conn, test_id)
    finally:
        conn.close()
    if not detail:
        raise HTTPException(404, "Test not found")
    return detail


@app.post("/api/v1/admin/tests")
def admin_create_test(body: TestCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn = get_db()
    try:
        qids, slug, scheduled_at = _prepare_test(conn, body)
        tid = secrets.token_hex(12)
        conn.execute("INSERT INTO tests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tid, body.series_id, body.title.strip(), slug, body.description, body.test_type, body.duration_minutes, len(qids), body.total_marks, body.negative_mark, body.access_type, scheduled_at, body.status, body.required_tier if body.access_type != "free" else 1))
        per_question = round(body.total_marks / max(len(qids), 1), 4)
        for i, qid in enumerate(qids, 1):
            conn.execute("INSERT INTO test_questions VALUES (?,?,?,?,?)", (tid, qid, i, per_question, body.negative_mark))
        conn.commit()
        return _test_detail(conn, tid) or {}
    except DB_INTEGRITY_ERROR:
        conn.rollback(); raise HTTPException(409, "Could not save this test (duplicate). Try a different title.")
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


@app.put("/api/v1/admin/tests/{test_id}")
def admin_update_test(test_id: str, body: TestCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn = get_db()
    try:
        existing = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if not existing:
            raise HTTPException(404, "Test not found")
        qids, slug, scheduled_at = _prepare_test(conn, body, exclude_id=test_id)
        attempts = conn.execute("SELECT COUNT(*) AS n FROM attempts WHERE test_id=?", (test_id,)).fetchone()["n"]
        if attempts:
            old_qids = [r["question_id"] for r in conn.execute("SELECT question_id FROM test_questions WHERE test_id=? ORDER BY question_order", (test_id,)).fetchall()]
            if old_qids != qids or abs(existing["total_marks"] - body.total_marks) > 1e-9 or abs(existing["negative_mark"] - body.negative_mark) > 1e-9:
                raise HTTPException(409, f"This test already has {attempts} student attempt(s), so its questions and marking can't be changed. Duplicate the test to make a new version.")
        conn.execute("UPDATE tests SET series_id=?,title=?,slug=?,description=?,test_type=?,duration_minutes=?,total_questions=?,total_marks=?,negative_mark=?,access_type=?,scheduled_at=?,status=?,required_tier=? WHERE id=?", (body.series_id, body.title.strip(), slug, body.description, body.test_type, body.duration_minutes, len(qids), body.total_marks, body.negative_mark, body.access_type, scheduled_at, body.status, body.required_tier if body.access_type != "free" else 1, test_id))
        if not attempts:
            conn.execute("DELETE FROM test_questions WHERE test_id=?", (test_id,))
            per_question = round(body.total_marks / max(len(qids), 1), 4)
            for i, qid in enumerate(qids, 1):
                conn.execute("INSERT INTO test_questions VALUES (?,?,?,?,?)", (test_id, qid, i, per_question, body.negative_mark))
        conn.commit()
        return _test_detail(conn, test_id) or {}
    except DB_INTEGRITY_ERROR:
        conn.rollback(); raise HTTPException(409, "Could not save this test (duplicate). Try a different title.")
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()

@app.get("/api/v1/admin/series")
def admin_series(admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT s.*,(SELECT COUNT(*) FROM tests t WHERE t.series_id=s.id) test_count FROM test_series s ORDER BY s.featured DESC,s.name").fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/series")
def admin_create_series(body: SeriesCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); sid=secrets.token_hex(12)
    try:
        base=slugify(body.slug) or "series"; slug,n=base,1
        while conn.execute("SELECT 1 FROM test_series WHERE slug=?",(slug,)).fetchone(): n+=1; slug=f"{base}-{n}"
        conn.execute("INSERT INTO test_series VALUES (?,?,?,?,?,?,?,?)",(sid,body.name.strip(),slug,body.description,body.price_paise,body.validity_days,body.status,int(body.featured))); conn.commit()
        row=conn.execute("SELECT * FROM test_series WHERE id=?",(sid,)).fetchone(); return dict(row)
    except DB_INTEGRITY_ERROR:
        conn.rollback(); raise HTTPException(409,"A series with this name already exists")
    finally:
        conn.close()

@app.put("/api/v1/admin/series/{series_id}")
def admin_update_series(series_id: str, body: SeriesCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); conn.execute("UPDATE test_series SET name=?,slug=?,description=?,price_paise=?,validity_days=?,status=?,featured=? WHERE id=?",(body.name,body.slug,body.description,body.price_paise,body.validity_days,body.status,int(body.featured),series_id)); conn.commit(); row=conn.execute("SELECT * FROM test_series WHERE id=?",(series_id,)).fetchone(); conn.close();
    if not row: raise HTTPException(404,"Series not found")
    return dict(row)

@app.get("/api/v1/admin/subscriptions")
def admin_subscriptions(status_filter: str | None = None, search: str | None = None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="""SELECT sub.id,sub.user_id,sub.plan_id,sub.status,sub.starts_at,sub.expires_at,p.name plan_name,p.price_paise,u.first_name,u.last_name,u.email,u.mobile FROM subscriptions sub JOIN plans p ON p.id=sub.plan_id JOIN users u ON u.id=sub.user_id WHERE 1=1"""; args=[]
    if status_filter: sql += " AND sub.status=?"; args.append(status_filter)
    if search: sql += " AND (u.first_name LIKE ? OR u.last_name LIKE ? OR u.email LIKE ? OR sub.id LIKE ?)"; args += [f"%{search}%"]*4
    rows=conn.execute(sql+" ORDER BY sub.expires_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/admin/plans")
def admin_plans(admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT p.*,(SELECT COUNT(*) FROM subscriptions s WHERE s.plan_id=p.id AND s.status='active') active_subscribers,(SELECT COUNT(*) FROM payments pay WHERE pay.plan_id=p.id AND pay.status='successful') successful_payments FROM plans p ORDER BY p.price_paise").fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/plans")
def admin_create_plan(body: PlanCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); pid=secrets.token_hex(12)
    try: conn.execute("INSERT INTO plans VALUES (?,?,?,?,?,?)",(pid,body.name,body.slug,body.price_paise,body.validity_days,int(body.is_public))); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Plan slug already exists")
    row=conn.execute("SELECT * FROM plans WHERE id=?",(pid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/plans/{plan_id}")
def admin_update_plan(plan_id: str, body: PlanCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cur=conn.execute("UPDATE plans SET name=?,slug=?,price_paise=?,validity_days=?,is_public=? WHERE id=?",(body.name,body.slug,body.price_paise,body.validity_days,int(body.is_public),plan_id)); conn.commit()
    row=conn.execute("SELECT * FROM plans WHERE id=?",(plan_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Plan not found")
    return dict(row)

@app.get("/api/v1/admin/payments")
def admin_payments(status_filter: str | None = None, gateway: str | None = None, method: str | None = None, search: str | None = None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="""SELECT pay.*,u.first_name,u.last_name,u.email,p.name plan_name FROM payments pay JOIN users u ON u.id=pay.user_id LEFT JOIN plans p ON p.id=pay.plan_id WHERE 1=1"""; args=[]
    if status_filter: sql += " AND pay.status=?"; args.append(status_filter)
    if gateway: sql += " AND pay.gateway=?"; args.append(gateway)
    if method: sql += " AND pay.method=?"; args.append(method)
    if search: sql += " AND (pay.transaction_id LIKE ? OR pay.order_id LIKE ? OR pay.invoice_number LIKE ? OR u.first_name LIKE ? OR u.last_name LIKE ?)"; args += [f"%{search}%"]*5
    rows=conn.execute(sql+" ORDER BY pay.created_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/payments/refund")
def admin_refund(body: RefundBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT * FROM payments WHERE id=?",(body.payment_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404,"Payment not found")
    if row["status"] != "successful": conn.close(); raise HTTPException(409,"Only successful payments can be refunded")
    amount=body.amount_paise if body.amount_paise is not None else row["amount_paise"]
    if amount<=0 or amount>row["amount_paise"]: conn.close(); raise HTTPException(400,"Invalid refund amount")
    if _payment_provider_name() == "razorpay" and row["gateway"] == "razorpay" and str(row["transaction_id"]).startswith("pay_"):
        try:
            get_razorpay().request_refund(row["transaction_id"], amount)
        except PaymentGatewayError as exc:
            conn.close(); logger.error("razorpay refund failed: %s", exc)
            raise HTTPException(502, f"Razorpay could not process the refund: {exc}")
    ts=now_iso(); status_value="refunded" if amount==row["amount_paise"] else "partially_refunded"
    subscription_cancelled = False
    if status_value == "refunded":                                       # a partial refund keeps the plan active
        sub = conn.execute("SELECT * FROM subscriptions WHERE payment_id=? AND status='active'", (body.payment_id,)).fetchone()
        if sub:
            conn.execute("UPDATE subscriptions SET status='cancelled' WHERE id=?", (sub["id"],))
            plan = conn.execute("SELECT name FROM plans WHERE id=?", (sub["plan_id"],)).fetchone()
            conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)", (
                secrets.token_hex(12), row["user_id"], "Plan access cancelled",
                f"Your {plan['name'] if plan else 'plan'} access was cancelled following a refund" + (f": {body.reason}" if body.reason else "."),
                "payment", 0, "subscription.html", ts))
            subscription_cancelled = True
    conn.execute("UPDATE payments SET status=?,refunded_at=?,refund_amount_paise=? WHERE id=?",(status_value,ts,amount,body.payment_id)); conn.commit(); out=dict(conn.execute("SELECT * FROM payments WHERE id=?",(body.payment_id,)).fetchone()); conn.close(); return {**out, "subscription_cancelled": subscription_cancelled}

@app.get("/api/v1/admin/coupons")
def admin_coupons(status_filter: str | None = None, search: str | None = None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="""SELECT c.*, (SELECT COUNT(*) FROM coupon_redemptions r WHERE r.coupon_id=c.id) redemptions FROM coupons c WHERE 1=1"""; args=[]
    if status_filter: sql += " AND c.status=?"; args.append(status_filter)
    if search: sql += " AND (c.code LIKE ? OR c.campaign LIKE ?)"; args += [f"%{search}%"]*2
    rows=conn.execute(sql+" ORDER BY c.ends_at DESC LIMIT 500",args).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post("/api/v1/admin/coupons")
def admin_create_coupon(body: CouponCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); cid=secrets.token_hex(12)
    try: conn.execute("INSERT INTO coupons VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(cid,body.code.upper(),body.campaign,body.discount_type,body.value,body.applies_to,body.starts_at,body.ends_at,body.redemption_limit,body.per_user_limit,body.min_order_paise,body.status)); conn.commit()
    except DB_INTEGRITY_ERROR: conn.close(); raise HTTPException(409,"Coupon code already exists")
    row=conn.execute("SELECT * FROM coupons WHERE id=?",(cid,)).fetchone(); conn.close(); return dict(row)

@app.put("/api/v1/admin/coupons/{coupon_id}")
def admin_update_coupon(coupon_id: str, body: CouponCreateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); conn.execute("UPDATE coupons SET code=?,campaign=?,discount_type=?,value=?,applies_to=?,starts_at=?,ends_at=?,redemption_limit=?,per_user_limit=?,min_order_paise=?,status=? WHERE id=?",(body.code.upper(),body.campaign,body.discount_type,body.value,body.applies_to,body.starts_at,body.ends_at,body.redemption_limit,body.per_user_limit,body.min_order_paise,body.status,coupon_id)); conn.commit(); row=conn.execute("SELECT * FROM coupons WHERE id=?",(coupon_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Coupon not found")
    return dict(row)

@app.get("/api/v1/admin/notifications")
def admin_notifications(status_filter: str | None = None, admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    conn=get_db(); sql="""SELECT n.title,n.body,n.type,n.href,n.created_at,COUNT(*) delivered,SUM(CASE WHEN n.is_read=1 THEN 1 ELSE 0 END) read_count FROM notifications n JOIN users u ON u.id=n.user_id WHERE u.role='student' GROUP BY n.title,n.body,n.type,n.href,n.created_at ORDER BY n.created_at DESC LIMIT 100"""; rows=conn.execute(sql).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/admin/reports/summary")
def admin_reports_summary(admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db()
    def scalar(sql, args=()): return conn.execute(sql,args).fetchone()[0]
    students=scalar("SELECT COUNT(*) FROM users WHERE role='student'")
    active=scalar("SELECT COUNT(*) FROM users WHERE role='student' AND status='active'")
    attempts=scalar("SELECT COUNT(*) FROM attempts WHERE status!='in_progress'")
    completed_tests=scalar("SELECT COUNT(*) FROM attempts WHERE status!='in_progress' AND submitted_at IS NOT NULL")
    successful_payments=scalar("SELECT COUNT(*) FROM payments WHERE status IN ('successful','partially_refunded')")
    gross=scalar("SELECT COALESCE(SUM(amount_paise),0) FROM payments WHERE status!='failed'")
    refunds=scalar("SELECT COALESCE(SUM(refund_amount_paise),0) FROM payments")
    avg_score=conn.execute("SELECT COALESCE(AVG(score),0) FROM attempts WHERE status!='in_progress'").fetchone()[0]
    subjects=[dict(r) for r in conn.execute("SELECT COALESCE(s.name,'Uncategorised') subject, COUNT(*) attempts, ROUND(100.0*SUM(CASE WHEN a.is_correct=1 THEN 1 ELSE 0 END)/NULLIF(SUM(CASE WHEN a.selected_option IS NOT NULL THEN 1 ELSE 0 END),0),1) accuracy FROM answers a JOIN attempts at ON at.id=a.attempt_id JOIN questions q ON q.id=a.question_id LEFT JOIN subjects s ON s.id=q.subject_id WHERE at.status!='in_progress' GROUP BY s.name ORDER BY accuracy DESC").fetchall()]
    conn.close(); return {"students":students,"active_students":active,"attempts":attempts,"completed_tests":completed_tests,"successful_payments":successful_payments,"gross_paise":gross,"refunds_paise":refunds,"net_paise":gross-refunds,"average_score":round(float(avg_score or 0),1),"subjects":subjects}

class TestEmailBody(BaseModel):
    to: EmailStr | None = None


@app.post("/api/v1/admin/notifications/test-email")
def admin_test_email(body: TestEmailBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    """Sends one real email through the configured SMTP settings, so the person setting up the account
    can confirm it actually works before relying on it for student sign-up or notifications."""
    if not smtp_configured():
        raise HTTPException(503, "SMTP is not configured yet: set SMTP_HOST and EMAIL_FROM (see deployment/README.md).")
    to = str(body.to) if body.to else admin["email"]
    if not to:
        raise HTTPException(400, "No destination address: your admin account has no e-mail on file, so specify one")
    result = send_email(to, "UPSC Test Series - test email", "This is a test email from your UPSC Test Series admin panel. If you received this, SMTP is configured correctly.")
    if not result.delivered:
        raise HTTPException(502, f"SMTP is configured but the test e-mail could not be sent: {result.message}")
    return {"delivered": True, "to": to}


@app.get("/api/v1/admin/settings")
def admin_settings(admin: Any = Depends(require_admin)) -> list[dict[str, Any]]:
    try:
        conn=get_db(); conn.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)"); rows=conn.execute("SELECT * FROM system_settings ORDER BY key").fetchall(); conn.close(); return [dict(r) for r in rows]
    except DB_ERROR as e: raise HTTPException(500,str(e))

@app.put("/api/v1/admin/settings")
def admin_setting(body: SettingsUpdateBody, admin: Any = Depends(require_admin)) -> dict[str, Any]:
    conn=get_db(); conn.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)"); conn.execute("INSERT INTO system_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(body.key,body.value)); conn.commit(); row=conn.execute("SELECT * FROM system_settings WHERE key=?",(body.key,)).fetchone(); conn.close(); return dict(row)

def calculate_coupon(conn: Database, user_id: str, plan: Any, code: str | None) -> tuple[Any | None, int]:
    if not code:
        return None, 0
    coupon = conn.execute("SELECT * FROM coupons WHERE upper(code)=upper(?)", (code.strip(),)).fetchone()
    if not coupon:
        raise HTTPException(404, "Coupon not found")
    now = datetime.now(timezone.utc)
    try:
        starts = datetime.fromisoformat(coupon["starts_at"]) if coupon["starts_at"] else None
        ends = datetime.fromisoformat(coupon["ends_at"]) if coupon["ends_at"] else None
    except Exception:
        starts = ends = None
    if coupon["status"] != "active" or (starts and now < starts) or (ends and now > ends):
        raise HTTPException(409, "Coupon is not currently active")
    applies_to = (coupon["applies_to"] or "all").strip().lower()
    if applies_to not in {"all", str(plan["id"]).lower(), str(plan["slug"]).lower()}:
        raise HTTPException(409, "Coupon is not applicable to this plan")
    if int(plan["price_paise"]) < int(coupon["min_order_paise"] or 0):
        raise HTTPException(409, "Minimum order value not met")
    total_redemptions = conn.execute("SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id=?", (coupon["id"],)).fetchone()[0]
    if coupon["redemption_limit"] is not None and total_redemptions >= coupon["redemption_limit"]:
        raise HTTPException(409, "Coupon redemption limit reached")
    user_redemptions = conn.execute("SELECT COUNT(*) FROM coupon_redemptions WHERE coupon_id=? AND user_id=?", (coupon["id"], user_id)).fetchone()[0]
    if user_redemptions >= int(coupon["per_user_limit"] or 1):
        raise HTTPException(409, "You have already used this coupon")
    subtotal = int(plan["price_paise"])
    if coupon["discount_type"] == "percentage":
        discount = round(subtotal * float(coupon["value"]) / 100)
    else:
        discount = int(round(float(coupon["value"]) * 100))
    # Existing demo flat coupons were authored as rupees, so convert values below 1000 to paise.
    max_discount = None
    if "max_discount_paise" in coupon.keys():
        max_discount = coupon["max_discount_paise"]
    if max_discount:
        discount = min(discount, int(max_discount))
    return coupon, min(discount, subtotal)

@app.post("/api/v1/payments/quote")
def payment_quote(body: CheckoutOrderBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn=get_db(); plan=conn.execute("SELECT * FROM plans WHERE id=? AND is_public=1", (body.plan_id,)).fetchone()
    if not plan:
        conn.close(); raise HTTPException(404, "Plan not found")
    try:
        coupon, discount = calculate_coupon(conn, user["id"], plan, body.coupon_code)
        subtotal = int(plan["price_paise"]); total=max(0, subtotal-discount)
        return {"plan":dict(plan),"coupon":dict(coupon) if coupon else None,"subtotal_paise":subtotal,"discount_paise":discount,"total_paise":total,"currency":"INR"}
    finally:
        conn.close()

def _payment_provider_name() -> str:
    return os.getenv("PAYMENT_PROVIDER", "development").strip().lower()


def get_razorpay() -> RazorpayProvider:
    cfg = RazorpayConfig.from_env()
    if not cfg.key_id or not cfg.key_secret:
        raise HTTPException(503, "Online payments are not configured yet. Please contact support.")
    return RazorpayProvider(cfg)


def activate_paid_order(conn: Database, order: Any, *, gateway: str, method: str, transaction_id: str) -> dict[str, Any]:
    """Turn a paid checkout order into a payment record + active subscription. Safe to call more than once."""
    claimed = conn.execute("UPDATE checkout_orders SET status='paid', paid_at=? WHERE id=? AND status!='paid'", (now_iso(), order["id"])).rowcount
    if claimed == 0:                                                     # already handled (e.g. webhook + browser both arrived)
        conn.rollback()
        payment = conn.execute("SELECT * FROM payments WHERE order_id=?", (order["provider_order_id"],)).fetchone()
        return {"status": "paid", "payment": dict(payment) if payment else None}
    ts = now_iso(); invoice = "INV-" + datetime.now().strftime("%Y%m%d") + "-" + secrets.token_hex(4).upper(); payid = secrets.token_hex(12)
    conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (payid, order["user_id"], order["plan_id"], transaction_id, order["provider_order_id"], invoice, gateway, method, order["total_paise"], "successful", ts, None, 0))
    plan = conn.execute("SELECT * FROM plans WHERE id=?", (order["plan_id"],)).fetchone()
    existing = conn.execute("SELECT * FROM subscriptions WHERE user_id=? AND plan_id=? AND status='active' ORDER BY expires_at DESC LIMIT 1", (order["user_id"], plan["id"])).fetchone()
    start = datetime.now(timezone.utc)
    if existing:
        try: start = max(start, datetime.fromisoformat(existing["expires_at"]))
        except Exception: pass
    expiry = start + timedelta(days=int(plan["validity_days"]))
    subid = secrets.token_hex(12)
    conn.execute("INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?)", (subid, order["user_id"], plan["id"], "active", start.isoformat(), expiry.isoformat(), payid))
    if order["coupon_id"]:
        conn.execute("INSERT INTO coupon_redemptions VALUES (?,?,?,?,?,?)", (secrets.token_hex(12), order["coupon_id"], order["user_id"], order["provider_order_id"], order["discount_paise"], ts))
    conn.execute("INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)", (secrets.token_hex(12), order["user_id"], "Payment successful", f"Your {plan['name']} access is active until {expiry.date().isoformat()}.", "payment", 0, "subscription.html", ts))
    conn.commit()
    out = dict(conn.execute("SELECT * FROM payments WHERE id=?", (payid,)).fetchone())
    return {"status": "paid", "payment": out, "subscription": {"id": subid, "plan_name": plan["name"], "starts_at": start.isoformat(), "expires_at": expiry.isoformat()}}


@app.post("/api/v1/payments/order")
def create_checkout_order(body: CheckoutOrderBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    provider_name = _payment_provider_name()
    if provider_name == "development" and IS_PRODUCTION:
        raise HTTPException(503, "Online payments are not available yet. Please contact support to activate a plan.")
    if provider_name not in {"development", "razorpay"}:
        raise HTTPException(501, "The configured payment provider is not supported")
    conn = get_db()
    try:
        plan = conn.execute("SELECT * FROM plans WHERE id=? AND is_public=1", (body.plan_id,)).fetchone()
        if not plan:
            raise HTTPException(404, "Plan not found")
        coupon, discount = calculate_coupon(conn, user["id"], plan, body.coupon_code)
        subtotal = int(plan["price_paise"]); total = max(0, subtotal - discount)
        rowid = secrets.token_hex(12); ts = now_iso(); coupon_id = coupon["id"] if coupon else None
        summary = {"currency": "INR", "plan": dict(plan), "discount_paise": discount, "subtotal_paise": subtotal, "checkout_order_id": rowid}

        if provider_name == "razorpay" and total == 0:                 # a 100% coupon: nothing to charge
            oid = "FREE-" + secrets.token_hex(8).upper()
            conn.execute("INSERT INTO checkout_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (rowid, user["id"], plan["id"], coupon_id, subtotal, discount, 0, "INR", "free", oid, "created", ts, None))
            result = activate_paid_order(conn, conn.execute("SELECT * FROM checkout_orders WHERE id=?", (rowid,)).fetchone(), gateway="coupon", method="coupon", transaction_id=oid)
            return {**summary, "provider": "free", "order_id": oid, "amount_paise": 0, "status": "paid", "subscription": result.get("subscription")}

        if provider_name == "razorpay":
            if total < 100:
                raise HTTPException(400, "The amount after discount is below the minimum online payment of \u20b91.")
            rz = get_razorpay()
            try:
                gw = rz.create_order(total, "rcpt_" + rowid, {"user_id": user["id"], "plan_id": plan["id"], "checkout_order_id": rowid})
            except PaymentGatewayError as exc:
                logger.error("razorpay create_order failed: %s", exc)
                raise HTTPException(502, "We could not start the payment. Please try again in a moment.")
            conn.execute("INSERT INTO checkout_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (rowid, user["id"], plan["id"], coupon_id, subtotal, discount, total, "INR", "razorpay", gw.order_id, "created", ts, None))
            conn.commit()
            name = " ".join(x for x in (user["first_name"], user["last_name"]) if x)
            return {**summary, "provider": "razorpay", "key_id": rz.key_id, "order_id": gw.order_id, "amount_paise": total, "status": "created",
                    "prefill": {"name": name, "email": user["email"] or "", "contact": user["mobile"] or ""}}

        oid = "ORD-" + secrets.token_hex(8).upper()
        conn.execute("INSERT INTO checkout_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (rowid, user["id"], plan["id"], coupon_id, subtotal, discount, total, "INR", "development", oid, "created", ts, None))
        conn.commit()
        return {**summary, "provider": "development", "order_id": oid, "amount_paise": total, "status": "created"}
    finally:
        conn.close()


class RazorpayVerifyBody(BaseModel):
    razorpay_order_id: str = Field(min_length=1, max_length=100)
    razorpay_payment_id: str = Field(min_length=1, max_length=100)
    razorpay_signature: str = Field(min_length=1, max_length=200)


@app.post("/api/v1/payments/razorpay/verify")
def razorpay_verify(body: RazorpayVerifyBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    """Called by the browser after Razorpay Checkout succeeds. Activates the plan only if the signature is genuine."""
    rz = get_razorpay()
    conn = get_db()
    try:
        order = conn.execute("SELECT * FROM checkout_orders WHERE provider_order_id=? AND user_id=? AND provider='razorpay'", (body.razorpay_order_id, user["id"])).fetchone()
        if not order:
            raise HTTPException(404, "Checkout order not found")
        if not rz.verify_payment_signature(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
            logger.warning("razorpay signature mismatch for order %s", body.razorpay_order_id)
            raise HTTPException(400, "Payment verification failed")
        return activate_paid_order(conn, order, gateway="razorpay", method="online", transaction_id=body.razorpay_payment_id)
    except DB_INTEGRITY_ERROR:
        conn.rollback()
        payment = conn.execute("SELECT * FROM payments WHERE order_id=?", (body.razorpay_order_id,)).fetchone()
        return {"status": "paid", "payment": dict(payment) if payment else None}
    finally:
        conn.close()


@app.post("/api/v1/payments/razorpay/webhook")
async def razorpay_webhook(request: Request) -> dict[str, Any]:
    """Razorpay calls this itself, so a plan is activated even if the student closes the browser after paying."""
    raw = await request.body()
    cfg = RazorpayConfig.from_env()
    if not cfg.webhook_secret:
        raise HTTPException(503, "Webhook is not configured")
    if not RazorpayProvider(cfg).verify_webhook(raw, request.headers.get("X-Razorpay-Signature", "")):
        logger.warning("razorpay webhook rejected: bad signature")
        raise HTTPException(400, "Invalid signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid payload")
    name = event.get("event", "")
    payload = event.get("payload") or {}
    pay = (payload.get("payment") or {}).get("entity") or {}
    order_id = pay.get("order_id") or ((payload.get("order") or {}).get("entity") or {}).get("id")
    payment_id = pay.get("id")
    if name not in {"payment.captured", "order.paid", "payment.failed"} or not order_id or not payment_id:
        return {"status": "ignored"}
    conn = get_db()
    try:
        order = conn.execute("SELECT * FROM checkout_orders WHERE provider_order_id=? AND provider='razorpay'", (order_id,)).fetchone()
        if not order:
            return {"status": "ignored"}
        if pay.get("amount") != order["total_paise"] or pay.get("currency", "INR") != "INR":
            logger.error("razorpay webhook amount mismatch for order %s", order_id)
            return {"status": "ignored"}
        if name == "payment.failed":
            if not conn.execute("SELECT 1 FROM payments WHERE transaction_id=?", (payment_id,)).fetchone():
                conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(12), order["user_id"], order["plan_id"], payment_id, order_id, None, "razorpay", pay.get("method") or "online", order["total_paise"], "failed", now_iso(), None, 0))
                conn.commit()
            return {"status": "recorded"}
        try:
            result = activate_paid_order(conn, order, gateway="razorpay", method=pay.get("method") or "online", transaction_id=payment_id)
        except DB_INTEGRITY_ERROR:
            conn.rollback(); result = {"status": "paid"}
        if pay.get("method"):                                           # the browser callback does not know the method; add it now
            conn.execute("UPDATE payments SET method=? WHERE order_id=? AND method='online'", (pay["method"], order_id)); conn.commit()
        return {"status": result["status"]}
    finally:
        conn.close()


@app.post("/api/v1/payments/development/confirm")
def development_confirm_payment(body: DevPaymentConfirmBody, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if IS_PRODUCTION or _payment_provider_name() != "development":
        raise HTTPException(403, "Development payment confirmation is disabled")
    conn = get_db()
    try:
        order = conn.execute("SELECT * FROM checkout_orders WHERE provider_order_id=? AND user_id=?", (body.order_id, user["id"])).fetchone()
        if not order:
            raise HTTPException(404, "Checkout order not found")
        return activate_paid_order(conn, order, gateway="development", method=body.method, transaction_id="DEVTXN-" + secrets.token_hex(7).upper())
    finally:
        conn.close()


@app.get("/api/v1/student/payments")
def student_payments(user: Any = Depends(get_current_user)) -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT p.*,pl.name plan_name FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id WHERE p.user_id=? ORDER BY p.created_at DESC", (user["id"],)).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get("/api/v1/student/invoices/{invoice_number}")
def student_invoice(invoice_number: str, user: Any = Depends(get_current_user)) -> dict[str, Any]:
    conn=get_db(); row=conn.execute("SELECT p.*,pl.name plan_name FROM payments p LEFT JOIN plans pl ON pl.id=p.plan_id WHERE p.invoice_number=? AND p.user_id=?", (invoice_number,user["id"])).fetchone(); conn.close()
    if not row: raise HTTPException(404,"Invoice not found")
    return {"invoice_number":row["invoice_number"],"payment_id":row["id"],"order_id":row["order_id"],"plan_name":row["plan_name"],"amount_paise":row["amount_paise"],"currency":"INR","issued_at":row["created_at"],"status":row["status"]}

@app.post("/api/v1/admin/files/upload")
async def admin_file_upload(file: UploadFile = File(...), access_type: str = Query(default='private'), required_plan_id: str | None = Query(default=None), admin: Any = Depends(require_admin)) -> dict[str, Any]:
    content = await file.read()
    try:
        stored = store(content, file.filename or 'upload.bin', file.content_type)
    except ValueError as exc:
        raise HTTPException(413, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    if access_type not in {"free", "premium", "private", "admin"}:
        raise HTTPException(400, "Invalid access_type")
    if access_type != "premium":
        required_plan_id = None
    conn = get_db(); file_id = secrets.token_hex(12); ts=now_iso()
    conn.execute("INSERT INTO file_assets VALUES (?,?,?,?,?,?,?,?,?)", (file_id, stored.storage_key, stored.original_name, stored.content_type, stored.size_bytes, admin['id'], access_type, required_plan_id, ts)); conn.commit(); conn.close()
    return {"id":file_id,"original_name":stored.original_name,"content_type":stored.content_type,"size_bytes":stored.size_bytes,"storage_key":stored.storage_key,"access_type":access_type,"required_plan_id":required_plan_id,"download_url":stored.public_url}


@app.get("/api/v1/files/{file_id}")
def get_file(file_id: str, user: Any = Depends(get_current_user)):
    conn=get_db(); row=conn.execute("SELECT * FROM file_assets WHERE id=?", (file_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404, "File not found")
    admin_roles = {'admin','super_admin','content_manager','evaluator','finance','support'}
    if row['access_type'] in {'admin','private'} and user['role'] not in admin_roles and user['id'] != row['uploaded_by']:
        raise HTTPException(403, "File access denied")
    if row['access_type'] == 'premium' and user['role'] not in admin_roles:
        if row['required_plan_id']:
            ent = get_db()
            try:
                active = ent.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND plan_id=? AND status='active' AND expires_at>? LIMIT 1", (user['id'], row['required_plan_id'], now_iso())).fetchone()
            finally:
                ent.close()
        else:
            ent = get_db()
            try:
                active = ent.execute("SELECT 1 FROM subscriptions WHERE user_id=? AND status='active' AND expires_at>? LIMIT 1", (user['id'], now_iso())).fetchone()
            finally:
                ent.close()
        if not active:
            raise HTTPException(403, "An active subscription is required to access this file")
    if os.getenv('OBJECT_STORAGE_PROVIDER','local').lower()=='local':
        root = Path(os.getenv('LOCAL_STORAGE_ROOT', BASE_DIR / 'data' / 'uploads')).resolve()
        target = (root / row['storage_key']).resolve()
        if root not in target.parents:
            raise HTTPException(400, "Invalid storage key")
        if not target.exists(): raise HTTPException(404, "Stored file not found")
        return FileResponse(target, media_type=row['content_type'], filename=row['original_name'])
    url=signed_url(row['storage_key'])
    if not url: raise HTTPException(503, "File delivery unavailable")
    return {"url":url,"expires_in_seconds":900}


def _series_summary(conn: Any, series_row: Any) -> dict[str, Any]:
    tests = conn.execute("SELECT test_type,total_questions,access_type FROM tests WHERE series_id=? AND status='published'", (series_row["id"],)).fetchall()
    return {
        "id": series_row["id"], "name": series_row["name"], "slug": series_row["slug"], "description": series_row["description"],
        "price_paise": series_row["price_paise"], "validity_days": series_row["validity_days"], "featured": bool(series_row["featured"]),
        "test_count": len(tests), "question_count": sum(t["total_questions"] for t in tests),
        "free_test_count": sum(1 for t in tests if t["access_type"] == "free"),
        "test_types": sorted({t["test_type"] for t in tests}),
    }


@app.get("/api/v1/public/series")
def public_series() -> list[dict[str, Any]]:
    """Published test series with live counts, for the public Test Series page."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM test_series WHERE status='published' ORDER BY featured DESC, name").fetchall()
        return [_series_summary(conn, r) for r in rows]
    finally:
        conn.close()


@app.get("/api/v1/public/series/{slug}")
def public_series_detail(slug: str) -> dict[str, Any]:
    """One published series and the (published) tests in it. Questions are never included."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM test_series WHERE slug=? AND status='published'", (slug,)).fetchone()
        if not row:
            raise HTTPException(404, "Test series not found")
        tests = conn.execute("SELECT id,title,description,test_type,duration_minutes,total_questions,total_marks,negative_mark,access_type,required_tier,scheduled_at FROM tests WHERE series_id=? AND status='published' ORDER BY title", (row["id"],)).fetchall()
        return {**_series_summary(conn, row), "tests": [dict(t) for t in tests]}
    finally:
        conn.close()


@app.get("/api/v1/public/free-tests")
def public_free_tests() -> list[dict[str, Any]]:
    """Published tests marked Free, with the subjects they cover, for the public Free Tests page."""
    conn = get_db()
    try:
        tests = conn.execute("SELECT t.id,t.title,t.description,t.test_type,t.duration_minutes,t.total_questions,t.total_marks,t.scheduled_at,s.name AS series_name FROM tests t LEFT JOIN test_series s ON s.id=t.series_id WHERE t.status='published' AND t.access_type='free' ORDER BY t.title").fetchall()
        out = []
        for t in tests:
            subs = conn.execute("SELECT DISTINCT sub.id,sub.name FROM test_questions tq JOIN questions q ON q.id=tq.question_id JOIN subjects sub ON sub.id=q.subject_id WHERE tq.test_id=? ORDER BY sub.name", (t["id"],)).fetchall()
            out.append({**dict(t), "subjects": [dict(x) for x in subs]})
        return out
    finally:
        conn.close()


@app.get("/api/v1/public/pyqs")
def public_pyqs(
    year: int | None = None,
    subject: str | None = None,
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    """Published previous-year questions (questions tagged with an UPSC exam year), with their answer keys."""
    conn = get_db()
    try:
        base = "FROM questions q LEFT JOIN subjects s ON s.id=q.subject_id LEFT JOIN topics t ON t.id=q.topic_id WHERE q.status='published' AND q.upsc_year IS NOT NULL"
        years = [r["upsc_year"] for r in conn.execute("SELECT DISTINCT q.upsc_year " + base + " ORDER BY q.upsc_year DESC").fetchall()]
        subjects = [dict(r) for r in conn.execute("SELECT DISTINCT s.id,s.name " + base + " AND s.id IS NOT NULL ORDER BY s.name").fetchall()]
        sql, args = "SELECT q.id,q.stem,q.option_a,q.option_b,q.option_c,q.option_d,q.correct_option,q.explanation,q.difficulty,q.upsc_year,q.source,q.subject_id,s.name AS subject_name,t.name AS topic_name " + base, []
        if year: sql += " AND q.upsc_year=?"; args.append(year)
        if subject: sql += " AND q.subject_id=?"; args.append(subject)
        if search: sql += " AND q.stem LIKE ?"; args.append(f"%{search}%")
        total = conn.execute("SELECT COUNT(*) AS n " + sql[sql.index("FROM questions"):], args).fetchone()["n"]
        rows = conn.execute(sql + " ORDER BY q.upsc_year DESC, q.updated_at DESC LIMIT ?", args + [limit]).fetchall()
        return {"total": total, "years": years, "subjects": subjects, "items": [dict(r) for r in rows]}
    finally:
        conn.close()


PUBLIC_RESULTS_MIN_PARTICIPANTS = 5


@app.get("/api/v1/public/stats")
def public_stats() -> dict[str, Any]:
    """Live counts for the home and series pages (published content only)."""
    conn = get_db()
    try:
        def count(sql: str) -> int:
            return int(conn.execute(sql).fetchone()["n"])
        by_tier = []
        for row in conn.execute("SELECT DISTINCT p.tier,p.name FROM plans p WHERE p.is_public=1 ORDER BY p.tier").fetchall():
            agg = conn.execute(
                "SELECT COUNT(*) AS n,COALESCE(SUM(total_questions),0) AS q FROM tests "
                "WHERE status='published' AND access_type!='free' AND required_tier<=?", (row["tier"],)).fetchone()
            by_tier.append({"tier": row["tier"], "plan_name": row["name"], "tests": agg["n"], "questions": agg["q"]})
        return {
            "questions": count("SELECT COUNT(*) AS n FROM questions WHERE status='published'"),
            "tests": count("SELECT COUNT(*) AS n FROM tests WHERE status='published'"),
            "series": count("SELECT COUNT(*) AS n FROM test_series WHERE status='published'"),
            "pyqs": count("SELECT COUNT(*) AS n FROM questions WHERE status='published' AND upsc_year IS NOT NULL"),
            "by_tier": by_tier,
        }
    finally:
        conn.close()


@app.get("/api/v1/public/results")
def public_results() -> dict[str, Any]:
    """Anonymous, aggregate results per test. Never includes names; tests with fewer than
    PUBLIC_RESULTS_MIN_PARTICIPANTS students are left out so nobody's score can be picked out."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT t.id,t.title,t.test_type,t.total_marks,t.total_questions,t.duration_minutes,t.access_type,"
            "COUNT(DISTINCT a.user_id) AS participants,COUNT(a.id) AS attempts,AVG(a.score) AS avg_score,MAX(a.score) AS top_score,MAX(a.submitted_at) AS last_at "
            "FROM tests t JOIN attempts a ON a.test_id=t.id AND a.status!='in_progress' AND a.score IS NOT NULL "
            "WHERE t.status='published' GROUP BY t.id,t.title,t.test_type,t.total_marks,t.total_questions,t.duration_minutes,t.access_type "
            "HAVING COUNT(DISTINCT a.user_id)>=? ORDER BY MAX(a.submitted_at) DESC", (PUBLIC_RESULTS_MIN_PARTICIPANTS,)).fetchall()
        items = []
        for r in rows:
            marks = float(r["total_marks"] or 0) or 1.0
            items.append({
                "id": r["id"], "title": r["title"], "test_type": r["test_type"], "access_type": r["access_type"],
                "total_marks": r["total_marks"], "total_questions": r["total_questions"], "duration_minutes": r["duration_minutes"],
                "participants": r["participants"], "attempts": r["attempts"], "last_at": r["last_at"],
                "avg_score": round(float(r["avg_score"]), 1), "top_score": round(float(r["top_score"]), 1),
                "avg_percent": round(float(r["avg_score"]) / marks * 100, 1), "top_percent": round(float(r["top_score"]) / marks * 100, 1),
            })
        total_attempts = sum(i["attempts"] for i in items)
        overall = {
            "tests": len(items), "participants": sum(i["participants"] for i in items), "attempts": total_attempts,
            "avg_percent": round(sum(i["avg_percent"] * i["attempts"] for i in items) / total_attempts, 1) if total_attempts else None,
            "top_percent": max((i["top_percent"] for i in items), default=None),
        }
        return {"min_participants": PUBLIC_RESULTS_MIN_PARTICIPANTS, "overall": overall, "items": items}
    finally:
        conn.close()


@app.get("/api/v1/plans")
def plans() -> list[dict[str, Any]]:
    conn=get_db(); rows=conn.execute("SELECT * FROM plans WHERE is_public=1 ORDER BY price_paise").fetchall(); conn.close(); return [dict(r) for r in rows]
