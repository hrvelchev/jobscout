CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS postings (
  posting_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source        TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  url           TEXT NOT NULL,
  company       TEXT NOT NULL,
  company_norm  TEXT NOT NULL,
  title         TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  location      TEXT,
  remote        BOOLEAN,
  salary_raw    TEXT,
  salary_min    INTEGER,
  salary_max    INTEGER,
  salary_currency TEXT,
  posted_at     TIMESTAMPTZ,
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  embedding     vector(384),
  duplicate_of  BIGINT REFERENCES postings(posting_id),
  status        TEXT NOT NULL DEFAULT 'new' CHECK (status IN
    ('new','scored','digested','applied','skipped','snoozed','closed')),
  snooze_until  TIMESTAMPTZ,
  closed_detected_at TIMESTAMPTZ,
  UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_postings_status ON postings(status, fetched_at);

CREATE TABLE IF NOT EXISTS scores (
  posting_id  BIGINT PRIMARY KEY REFERENCES postings ON DELETE CASCADE,
  fit_score   INTEGER NOT NULL CHECK (fit_score BETWEEN 0 AND 100),
  lane        TEXT NOT NULL,
  stack_match INTEGER NOT NULL,
  seniority_gap INTEGER NOT NULL,
  degree_gate TEXT NOT NULL,
  red_flags   JSONB NOT NULL DEFAULT '[]',
  cv_keywords JSONB NOT NULL DEFAULT '[]',
  reason      TEXT NOT NULL DEFAULT '',
  scored_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drafts (
  draft_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_id   BIGINT NOT NULL REFERENCES postings ON DELETE CASCADE,
  cv_variant   TEXT NOT NULL,
  note_text    TEXT NOT NULL,
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
  event_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  posting_id  BIGINT REFERENCES postings,
  event_type  TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scan_log (
  scan_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source      TEXT NOT NULL,
  external_id TEXT NOT NULL,
  url         TEXT NOT NULL DEFAULT '',
  company     TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL DEFAULT '',
  verdict     TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '',
  scanned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS app_state (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS usage_log (
  log_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  model         TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_usd      NUMERIC(10,6) NOT NULL,
  purpose       TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW applications AS
  SELECT p.posting_id, p.company, p.title, p.url, p.status,
         (SELECT max(e.created_at) FROM events e
           WHERE e.posting_id = p.posting_id AND e.to_status = 'applied') AS applied_at,
         (SELECT d.cv_variant FROM drafts d WHERE d.posting_id = p.posting_id
           ORDER BY d.generated_at DESC LIMIT 1) AS cv_variant
  FROM postings p
  WHERE p.status IN ('applied','closed');
