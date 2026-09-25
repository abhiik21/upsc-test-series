-- UPSC Test Series Platform
-- Runtime PostgreSQL schema used by backend/app.py.
-- The application repository intentionally uses text IDs for portability.

BEGIN;

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
 status TEXT NOT NULL DEFAULT 'published'
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
 is_public INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS subscriptions (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL,
 plan_id TEXT NOT NULL,
 status TEXT NOT NULL,
 starts_at TEXT NOT NULL,
 expires_at TEXT NOT NULL
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

-- CMS columns added by the application migration layer.
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS author TEXT;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS featured INTEGER NOT NULL DEFAULT 0;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS mcq_link TEXT;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS seo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE current_affairs ADD COLUMN IF NOT EXISTS content TEXT;

ALTER TABLE study_material ADD COLUMN IF NOT EXISTS owner TEXT;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS tags TEXT;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS featured INTEGER NOT NULL DEFAULT 0;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS track INTEGER NOT NULL DEFAULT 0;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS practice TEXT;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS seo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE study_material ADD COLUMN IF NOT EXISTS file_url TEXT;

ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS format TEXT;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS image TEXT;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS seo_title TEXT;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS meta_description TEXT;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS featured INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS newsletter INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS related_tests TEXT;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS allow_comments INTEGER NOT NULL DEFAULT 0;
ALTER TABLE blog_posts ADD COLUMN IF NOT EXISTS content TEXT;

CREATE TABLE IF NOT EXISTS system_settings (
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_role_status ON users(role, status);
CREATE INDEX IF NOT EXISTS idx_questions_subject_topic ON questions(subject_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_tests_series_status ON tests(series_id, status);
CREATE INDEX IF NOT EXISTS idx_attempts_user_status ON attempts(user_id, status);
CREATE INDEX IF NOT EXISTS idx_attempts_test ON attempts(test_id);
CREATE INDEX IF NOT EXISTS idx_answers_attempt ON answers(attempt_id);
CREATE INDEX IF NOT EXISTS idx_payments_user_created ON payments(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_current_affairs_status_date ON current_affairs(status, published_at);
CREATE INDEX IF NOT EXISTS idx_blog_posts_status_date ON blog_posts(status, published_at);

COMMIT;
