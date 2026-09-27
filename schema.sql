-- UPSC Test Series Platform
-- PostgreSQL schema - v1
-- Designed for student web/mobile clients + admin panel.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

BEGIN;

-- ---------- ENUMS ----------
DO $$ BEGIN
  CREATE TYPE user_role AS ENUM ('student','content_manager','evaluator','support','finance','admin','super_admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE user_status AS ENUM ('active','inactive','blocked','pending_verification');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE content_status AS ENUM ('draft','needs_review','scheduled','published','archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE difficulty_level AS ENUM ('easy','moderate','difficult');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE test_type AS ENUM ('subject','topic','mixed','full_length','csat','current_affairs','pyq','free');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE access_type AS ENUM ('free','premium','included');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE attempt_status AS ENUM ('in_progress','submitted','auto_submitted','evaluated','cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE payment_status AS ENUM ('created','pending','successful','failed','refunded','partially_refunded','cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE subscription_status AS ENUM ('trial','active','expired','cancelled','paused');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE notification_status AS ENUM ('draft','scheduled','sending','sent','failed','cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- USERS / AUTH ----------
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  first_name VARCHAR(80) NOT NULL,
  last_name VARCHAR(80),
  email VARCHAR(320) UNIQUE,
  mobile VARCHAR(20) UNIQUE,
  password_hash TEXT,
  role user_role NOT NULL DEFAULT 'student',
  status user_status NOT NULL DEFAULT 'pending_verification',
  target_exam_year SMALLINT,
  preparation_stage VARCHAR(40),
  profile_photo_url TEXT,
  last_login_at TIMESTAMPTZ,
  email_verified_at TIMESTAMPTZ,
  mobile_verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (email IS NOT NULL OR mobile IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_token_hash TEXT NOT NULL,
  user_agent TEXT,
  ip_address INET,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS otp_verifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  channel VARCHAR(12) NOT NULL CHECK (channel IN ('email','mobile')),
  destination VARCHAR(320) NOT NULL,
  code_hash TEXT NOT NULL,
  purpose VARCHAR(40) NOT NULL,
  attempts SMALLINT NOT NULL DEFAULT 0,
  expires_at TIMESTAMPTZ NOT NULL,
  verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- AUTHORIZATION ----------
CREATE TABLE IF NOT EXISTS permissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code VARCHAR(100) UNIQUE NOT NULL,
  description TEXT
);

CREATE TABLE IF NOT EXISTS role_permissions (
  role user_role NOT NULL,
  permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role, permission_id)
);

-- ---------- ACADEMIC TAXONOMY ----------
CREATE TABLE IF NOT EXISTS subjects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(140) UNIQUE NOT NULL,
  description TEXT,
  display_order INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id UUID NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
  name VARCHAR(160) NOT NULL,
  slug VARCHAR(180) NOT NULL,
  description TEXT,
  display_order INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE(subject_id, slug)
);

CREATE TABLE IF NOT EXISTS subtopics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id UUID NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  name VARCHAR(180) NOT NULL,
  slug VARCHAR(200) NOT NULL,
  display_order INTEGER NOT NULL DEFAULT 0,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  UNIQUE(topic_id, slug)
);

CREATE TABLE IF NOT EXISTS content_sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  source_type VARCHAR(50),
  url TEXT,
  citation TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tags (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(80) UNIQUE NOT NULL,
  slug VARCHAR(100) UNIQUE NOT NULL
);

-- ---------- QUESTION BANK ----------
CREATE TABLE IF NOT EXISTS questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
  topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
  subtopic_id UUID REFERENCES subtopics(id) ON DELETE SET NULL,
  stem TEXT NOT NULL,
  explanation TEXT,
  difficulty difficulty_level NOT NULL DEFAULT 'moderate',
  correct_option CHAR(1),
  source_id UUID REFERENCES content_sources(id) ON DELETE SET NULL,
  source_year SMALLINT,
  upsc_year SMALLINT,
  question_type VARCHAR(40) NOT NULL DEFAULT 'mcq',
  estimated_time_seconds SMALLINT,
  status content_status NOT NULL DEFAULT 'draft',
  reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at TIMESTAMPTZ,
  usage_count INTEGER NOT NULL DEFAULT 0,
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (correct_option IS NULL OR correct_option IN ('A','B','C','D','E'))
);

CREATE TABLE IF NOT EXISTS question_options (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  option_key CHAR(1) NOT NULL,
  option_text TEXT NOT NULL,
  display_order SMALLINT NOT NULL,
  UNIQUE(question_id, option_key),
  UNIQUE(question_id, display_order)
);

CREATE TABLE IF NOT EXISTS question_tags (
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(question_id, tag_id)
);

-- ---------- TESTS / SERIES ----------
CREATE TABLE IF NOT EXISTS test_series (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  slug VARCHAR(280) UNIQUE NOT NULL,
  description TEXT,
  exam_type VARCHAR(50) NOT NULL DEFAULT 'prelims',
  access_type access_type NOT NULL DEFAULT 'premium',
  price_paise BIGINT NOT NULL DEFAULT 0,
  validity_days INTEGER,
  status content_status NOT NULL DEFAULT 'draft',
  launch_at TIMESTAMPTZ,
  purchase_before_release BOOLEAN NOT NULL DEFAULT TRUE,
  featured BOOLEAN NOT NULL DEFAULT FALSE,
  display_order INTEGER NOT NULL DEFAULT 0,
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(255) NOT NULL,
  slug VARCHAR(280) UNIQUE NOT NULL,
  description TEXT,
  series_id UUID REFERENCES test_series(id) ON DELETE SET NULL,
  test_type test_type NOT NULL,
  access_type access_type NOT NULL DEFAULT 'premium',
  duration_minutes INTEGER NOT NULL,
  total_questions INTEGER NOT NULL DEFAULT 0,
  total_marks NUMERIC(8,2) NOT NULL DEFAULT 0,
  default_mark_per_question NUMERIC(8,2),
  default_negative_mark NUMERIC(8,2) NOT NULL DEFAULT 0,
  passing_marks NUMERIC(8,2),
  status content_status NOT NULL DEFAULT 'draft',
  scheduled_at TIMESTAMPTZ,
  published_at TIMESTAMPTZ,
  allow_retake BOOLEAN NOT NULL DEFAULT TRUE,
  show_solutions_after_submit BOOLEAN NOT NULL DEFAULT TRUE,
  show_ranking BOOLEAN NOT NULL DEFAULT TRUE,
  randomize_questions BOOLEAN NOT NULL DEFAULT FALSE,
  prevent_duplicate_questions BOOLEAN NOT NULL DEFAULT TRUE,
  auto_submit_on_timeout BOOLEAN NOT NULL DEFAULT TRUE,
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS series_tests (
  series_id UUID NOT NULL REFERENCES test_series(id) ON DELETE CASCADE,
  test_id UUID NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  sequence_no INTEGER NOT NULL,
  release_at TIMESTAMPTZ,
  PRIMARY KEY(series_id, test_id),
  UNIQUE(series_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS test_questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  test_id UUID NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  question_order INTEGER NOT NULL,
  marks NUMERIC(8,2) NOT NULL DEFAULT 2,
  negative_marks NUMERIC(8,2) NOT NULL DEFAULT 0.67,
  section_name VARCHAR(100),
  UNIQUE(test_id, question_order),
  UNIQUE(test_id, question_id)
);

-- ---------- ATTEMPTS / EVALUATION ----------
CREATE TABLE IF NOT EXISTS attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  test_id UUID NOT NULL REFERENCES tests(id) ON DELETE RESTRICT,
  attempt_number INTEGER NOT NULL DEFAULT 1,
  status attempt_status NOT NULL DEFAULT 'in_progress',
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  submitted_at TIMESTAMPTZ,
  evaluated_at TIMESTAMPTZ,
  time_taken_seconds INTEGER,
  score NUMERIC(10,2) NOT NULL DEFAULT 0,
  correct_count INTEGER NOT NULL DEFAULT 0,
  incorrect_count INTEGER NOT NULL DEFAULT 0,
  unattempted_count INTEGER NOT NULL DEFAULT 0,
  accuracy NUMERIC(6,3) NOT NULL DEFAULT 0,
  percentile NUMERIC(7,3),
  rank INTEGER,
  review_required BOOLEAN NOT NULL DEFAULT FALSE,
  review_note TEXT,
  UNIQUE(user_id, test_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS attempt_answers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id UUID NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE RESTRICT,
  selected_option CHAR(1),
  is_correct BOOLEAN,
  marks_awarded NUMERIC(8,2) NOT NULL DEFAULT 0,
  time_spent_seconds INTEGER NOT NULL DEFAULT 0,
  marked_for_review BOOLEAN NOT NULL DEFAULT FALSE,
  answered_at TIMESTAMPTZ,
  UNIQUE(attempt_id, question_id)
);

CREATE TABLE IF NOT EXISTS result_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id UUID UNIQUE NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
  score NUMERIC(10,2) NOT NULL,
  correct_count INTEGER NOT NULL,
  incorrect_count INTEGER NOT NULL,
  unattempted_count INTEGER NOT NULL,
  accuracy NUMERIC(6,3) NOT NULL,
  rank INTEGER,
  percentile NUMERIC(7,3),
  subject_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
  topic_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- PLANS / SUBSCRIPTIONS ----------
CREATE TABLE IF NOT EXISTS plans (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(160) NOT NULL,
  slug VARCHAR(180) UNIQUE NOT NULL,
  description TEXT,
  price_paise BIGINT NOT NULL DEFAULT 0,
  validity_days INTEGER,
  trial_days INTEGER NOT NULL DEFAULT 0,
  max_devices SMALLINT,
  auto_renew_allowed BOOLEAN NOT NULL DEFAULT FALSE,
  is_public BOOLEAN NOT NULL DEFAULT TRUE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS plan_entitlements (
  plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  entitlement_code VARCHAR(100) NOT NULL,
  PRIMARY KEY(plan_id, entitlement_code)
);

CREATE TABLE IF NOT EXISTS plan_test_series (
  plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  series_id UUID NOT NULL REFERENCES test_series(id) ON DELETE CASCADE,
  PRIMARY KEY(plan_id, series_id)
);

CREATE TABLE IF NOT EXISTS user_subscriptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
  status subscription_status NOT NULL DEFAULT 'active',
  started_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  cancelled_at TIMESTAMPTZ,
  auto_renew BOOLEAN NOT NULL DEFAULT FALSE,
  source_payment_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- PAYMENTS / INVOICES ----------
CREATE TABLE IF NOT EXISTS payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  plan_id UUID REFERENCES plans(id) ON DELETE SET NULL,
  test_series_id UUID REFERENCES test_series(id) ON DELETE SET NULL,
  order_id VARCHAR(120) UNIQUE NOT NULL,
  gateway VARCHAR(50) NOT NULL,
  gateway_payment_id VARCHAR(160),
  gateway_signature TEXT,
  amount_paise BIGINT NOT NULL,
  currency CHAR(3) NOT NULL DEFAULT 'INR',
  status payment_status NOT NULL DEFAULT 'created',
  payment_method VARCHAR(50),
  coupon_id UUID,
  failure_code VARCHAR(100),
  failure_message TEXT,
  paid_at TIMESTAMPTZ,
  refunded_at TIMESTAMPTZ,
  raw_webhook JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invoices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  payment_id UUID UNIQUE NOT NULL REFERENCES payments(id) ON DELETE RESTRICT,
  invoice_number VARCHAR(120) UNIQUE NOT NULL,
  subtotal_paise BIGINT NOT NULL,
  tax_paise BIGINT NOT NULL DEFAULT 0,
  discount_paise BIGINT NOT NULL DEFAULT 0,
  total_paise BIGINT NOT NULL,
  invoice_url TEXT,
  issued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE user_subscriptions
  ADD CONSTRAINT fk_user_subscription_payment
  FOREIGN KEY (source_payment_id) REFERENCES payments(id) ON DELETE SET NULL;

-- ---------- COUPONS ----------
CREATE TABLE IF NOT EXISTS coupons (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  code VARCHAR(60) UNIQUE NOT NULL,
  description TEXT,
  discount_type VARCHAR(20) NOT NULL CHECK (discount_type IN ('percentage','flat')),
  discount_value NUMERIC(12,2) NOT NULL,
  max_discount_paise BIGINT,
  min_order_paise BIGINT NOT NULL DEFAULT 0,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_until TIMESTAMPTZ NOT NULL,
  global_limit INTEGER,
  per_user_limit INTEGER,
  first_time_only BOOLEAN NOT NULL DEFAULT FALSE,
  allow_stacking BOOLEAN NOT NULL DEFAULT FALSE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (valid_until > valid_from)
);

CREATE TABLE IF NOT EXISTS coupon_plans (
  coupon_id UUID NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
  plan_id UUID NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  PRIMARY KEY(coupon_id, plan_id)
);

CREATE TABLE IF NOT EXISTS coupon_series (
  coupon_id UUID NOT NULL REFERENCES coupons(id) ON DELETE CASCADE,
  series_id UUID NOT NULL REFERENCES test_series(id) ON DELETE CASCADE,
  PRIMARY KEY(coupon_id, series_id)
);

CREATE TABLE IF NOT EXISTS coupon_redemptions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  coupon_id UUID NOT NULL REFERENCES coupons(id) ON DELETE RESTRICT,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  payment_id UUID REFERENCES payments(id) ON DELETE SET NULL,
  discount_paise BIGINT NOT NULL,
  redeemed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE payments
  ADD CONSTRAINT fk_payment_coupon
  FOREIGN KEY (coupon_id) REFERENCES coupons(id) ON DELETE SET NULL;

-- ---------- CURRENT AFFAIRS ----------
CREATE TABLE IF NOT EXISTS current_affairs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(320) NOT NULL,
  slug VARCHAR(360) UNIQUE NOT NULL,
  summary TEXT,
  body TEXT NOT NULL,
  category VARCHAR(80) NOT NULL,
  gs_area VARCHAR(80),
  event_date DATE,
  month_key CHAR(7),
  status content_status NOT NULL DEFAULT 'draft',
  featured BOOLEAN NOT NULL DEFAULT FALSE,
  revision_eligible BOOLEAN NOT NULL DEFAULT TRUE,
  practice_question_count INTEGER NOT NULL DEFAULT 0,
  view_count INTEGER NOT NULL DEFAULT 0,
  seo_title VARCHAR(320),
  meta_description VARCHAR(500),
  canonical_url TEXT,
  source_id UUID REFERENCES content_sources(id) ON DELETE SET NULL,
  author_id UUID REFERENCES users(id) ON DELETE SET NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS current_affairs_tags (
  article_id UUID NOT NULL REFERENCES current_affairs(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(article_id, tag_id)
);

-- ---------- STUDY MATERIAL ----------
CREATE TABLE IF NOT EXISTS study_material (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(320) NOT NULL,
  slug VARCHAR(360) UNIQUE NOT NULL,
  description TEXT,
  subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
  topic_id UUID REFERENCES topics(id) ON DELETE SET NULL,
  format VARCHAR(40) NOT NULL,
  access_type access_type NOT NULL DEFAULT 'free',
  file_url TEXT,
  file_size_bytes BIGINT,
  page_count INTEGER,
  status content_status NOT NULL DEFAULT 'draft',
  download_count INTEGER NOT NULL DEFAULT 0,
  view_count INTEGER NOT NULL DEFAULT 0,
  progress_tracking_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  practice_test_id UUID REFERENCES tests(id) ON DELETE SET NULL,
  seo_title VARCHAR(320),
  meta_description VARCHAR(500),
  source_id UUID REFERENCES content_sources(id) ON DELETE SET NULL,
  uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- BLOG / CMS ----------
CREATE TABLE IF NOT EXISTS blog_categories (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(120) NOT NULL,
  slug VARCHAR(140) UNIQUE NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS blog_posts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(320) NOT NULL,
  slug VARCHAR(360) UNIQUE NOT NULL,
  excerpt TEXT,
  body TEXT NOT NULL,
  category_id UUID REFERENCES blog_categories(id) ON DELETE SET NULL,
  author_id UUID REFERENCES users(id) ON DELETE SET NULL,
  status content_status NOT NULL DEFAULT 'draft',
  featured BOOLEAN NOT NULL DEFAULT FALSE,
  include_newsletter BOOLEAN NOT NULL DEFAULT FALSE,
  revision_eligible BOOLEAN NOT NULL DEFAULT FALSE,
  featured_image_url TEXT,
  seo_title VARCHAR(320),
  meta_description VARCHAR(500),
  canonical_url TEXT,
  view_count INTEGER NOT NULL DEFAULT 0,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS blog_post_tags (
  post_id UUID NOT NULL REFERENCES blog_posts(id) ON DELETE CASCADE,
  tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY(post_id, tag_id)
);

-- ---------- STUDENT PERSONALIZATION ----------
CREATE TABLE IF NOT EXISTS bookmarks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content_type VARCHAR(40) NOT NULL,
  content_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, content_type, content_id)
);

CREATE TABLE IF NOT EXISTS mistakes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  question_id UUID NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  source_attempt_id UUID REFERENCES attempts(id) ON DELETE SET NULL,
  reason_code VARCHAR(40),
  note TEXT,
  review_due_at TIMESTAMPTZ,
  resolved_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, question_id)
);

CREATE TABLE IF NOT EXISTS material_progress (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  material_id UUID NOT NULL REFERENCES study_material(id) ON DELETE CASCADE,
  progress_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
  last_position INTEGER,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, material_id)
);

CREATE TABLE IF NOT EXISTS current_affairs_read_state (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  article_id UUID NOT NULL REFERENCES current_affairs(id) ON DELETE CASCADE,
  read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  saved BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(user_id, article_id)
);

-- ---------- NOTIFICATIONS ----------
CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(255) NOT NULL,
  body TEXT NOT NULL,
  notification_type VARCHAR(50) NOT NULL,
  status notification_status NOT NULL DEFAULT 'draft',
  priority VARCHAR(20) NOT NULL DEFAULT 'normal',
  audience_type VARCHAR(40) NOT NULL DEFAULT 'all',
  scheduled_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  deep_link TEXT,
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification_recipients (
  notification_id UUID NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  delivered_at TIMESTAMPTZ,
  read_at TIMESTAMPTZ,
  PRIMARY KEY(notification_id, user_id)
);

CREATE TABLE IF NOT EXISTS notification_preferences (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  email_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  push_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  sms_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  marketing_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  test_reminders BOOLEAN NOT NULL DEFAULT TRUE,
  result_alerts BOOLEAN NOT NULL DEFAULT TRUE,
  content_alerts BOOLEAN NOT NULL DEFAULT TRUE,
  subscription_alerts BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- SUPPORT / AUDIT / SYSTEM ----------
CREATE TABLE IF NOT EXISTS enquiries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  name VARCHAR(160) NOT NULL,
  email VARCHAR(320),
  mobile VARCHAR(20),
  category VARCHAR(60) NOT NULL,
  subject VARCHAR(255) NOT NULL,
  message TEXT NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'open',
  assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGSERIAL PRIMARY KEY,
  actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  action VARCHAR(100) NOT NULL,
  entity_type VARCHAR(60),
  entity_id UUID,
  old_data JSONB,
  new_data JSONB,
  ip_address INET,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_settings (
  key VARCHAR(160) PRIMARY KEY,
  value JSONB NOT NULL,
  description TEXT,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_metrics (
  metric_date DATE NOT NULL,
  metric_key VARCHAR(100) NOT NULL,
  metric_value NUMERIC(18,4) NOT NULL DEFAULT 0,
  PRIMARY KEY(metric_date, metric_key)
);

-- ---------- INDEXES ----------
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_questions_taxonomy ON questions(subject_id, topic_id, subtopic_id);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_tests_series_status ON tests(series_id, status);
CREATE INDEX IF NOT EXISTS idx_tests_release ON tests(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_test ON attempts(test_id, score DESC);
CREATE INDEX IF NOT EXISTS idx_attempt_answers_attempt ON attempt_answers(attempt_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_user_status ON user_subscriptions(user_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_payments_status_created ON payments(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_coupon_redemptions_user ON coupon_redemptions(user_id, coupon_id);
CREATE INDEX IF NOT EXISTS idx_current_affairs_status_date ON current_affairs(status, event_date DESC);
CREATE INDEX IF NOT EXISTS idx_material_status ON study_material(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_blog_status_date ON blog_posts(status, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mistakes_user_due ON mistakes(user_id, review_due_at);
CREATE INDEX IF NOT EXISTS idx_notifications_status_schedule ON notifications(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id, created_at DESC);

-- ---------- UPDATED_AT TRIGGER ----------
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'users','subjects','test_series','tests','questions','plans','user_subscriptions',
    'payments','current_affairs','study_material','blog_posts','mistakes','material_progress',
    'notifications','notification_preferences','system_settings'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_updated_at ON %I', t, t);
    EXECUTE format('CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;

COMMIT;


-- Added in v49 student/admin integration
CREATE TABLE IF NOT EXISTS notifications (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 type TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, href TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS current_affairs (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, gs TEXT, month TEXT,
 published_at TEXT NOT NULL, minutes INTEGER NOT NULL DEFAULT 5, summary TEXT NOT NULL, key_points TEXT,
 status TEXT NOT NULL DEFAULT 'published'
);
CREATE TABLE IF NOT EXISTS study_material (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, subject TEXT NOT NULL, topic TEXT, format TEXT NOT NULL,
 access_type TEXT NOT NULL DEFAULT 'free', pages TEXT, summary TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'published'
);
CREATE TABLE IF NOT EXISTS blog_posts (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT UNIQUE NOT NULL, excerpt TEXT, category TEXT, author TEXT,
 status TEXT NOT NULL DEFAULT 'published', published_at TEXT
);
CREATE TABLE IF NOT EXISTS support_enquiries (
 id TEXT PRIMARY KEY, user_id TEXT, name TEXT NOT NULL, email TEXT, subject TEXT NOT NULL, message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
);
