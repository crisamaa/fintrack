-- Fintrack schema v1. Market data only: nothing personal about the user is stored here, because the repository
-- (and therefore this database's JSONL dump) is public. The user's profile, applications and contacts live in
-- the browser (site/store.js).
--
-- Timestamps are ISO-8601 UTC strings ("2026-11-04T05:00:00Z"); calendar dates are "YYYY-MM-DD".
-- JSON columns hold small lists/objects that are never queried on their own.

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Employers -------------------------------------------------------------------------------------------------------
CREATE TABLE employers (
  id            TEXT PRIMARY KEY,                -- stable slug; legacy firm ids are kept as-is
  name          TEXT NOT NULL,
  name_key      TEXT NOT NULL,                   -- folded name without legal forms (tracker.names.name_key)
  website       TEXT,                            -- official site origin, e.g. https://www.vontobel.com
  domain        TEXT,                            -- registrable domain, e.g. vontobel.com
  hq_country    TEXT,
  parent_id     TEXT REFERENCES employers(id) ON DELETE SET NULL,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'merged', 'rejected')),
  merged_into   TEXT REFERENCES employers(id) ON DELETE SET NULL,
  size_hint     TEXT CHECK (size_hint IN ('large', 'mid', 'boutique')),
  priority      INTEGER NOT NULL DEFAULT 2 CHECK (priority IN (1, 2, 3)),  -- 1 high, 2 normal, 3 low
  access_note   TEXT,                            -- e.g. "refuses automated visits: check by hand"
  discovered_via TEXT NOT NULL,                  -- seed | finma | posting | search | directory | import | benchmark
  discovery_evidence TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX employers_name_key ON employers(name_key);
CREATE INDEX employers_domain ON employers(domain);

CREATE TABLE employer_aliases (
  employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
  alias       TEXT NOT NULL,
  alias_key   TEXT NOT NULL,
  source      TEXT NOT NULL,
  PRIMARY KEY (employer_id, alias_key)
);
CREATE INDEX employer_aliases_key ON employer_aliases(alias_key);

-- Overlapping sector classification (tracker.taxonomy.SECTORS).
CREATE TABLE employer_sectors (
  employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
  sector      TEXT NOT NULL,
  method      TEXT NOT NULL CHECK (method IN ('seed', 'rule', 'directory', 'ai', 'manual')),
  PRIMARY KEY (employer_id, sector)
);

CREATE TABLE employer_locations (
  employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
  country     TEXT NOT NULL,
  city        TEXT NOT NULL DEFAULT '',
  method      TEXT NOT NULL,
  PRIMARY KEY (employer_id, country, city)
);

-- Names found by discovery that are not (yet) employers.
CREATE TABLE employer_candidates (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL,
  name_key       TEXT NOT NULL,
  country        TEXT,
  website        TEXT,
  sector_hint    TEXT,
  origin         TEXT NOT NULL,                  -- finma | posting | search | directory:<id> | seed | coverage
  evidence_url   TEXT,
  evidence       TEXT,
  status         TEXT NOT NULL DEFAULT 'new'
                 CHECK (status IN ('new', 'accepted', 'rejected', 'unresolved', 'duplicate')),
  employer_id    TEXT REFERENCES employers(id) ON DELETE SET NULL,
  attempts       INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT,
  note           TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  UNIQUE (name_key, origin)
);

-- Recruitment sources -----------------------------------------------------------------------------------------------
CREATE TABLE sources (
  id               TEXT PRIMARY KEY,             -- "<kind>:<key>", e.g. greenhouse:21shares, page:3f9a01bc
  employer_id      TEXT REFERENCES employers(id) ON DELETE SET NULL,
  association      TEXT NOT NULL DEFAULT 'verified' CHECK (association IN ('verified', 'probable', 'unverified')),
  kind             TEXT NOT NULL,                -- connector name (tracker.connectors.CONNECTORS)
  board            TEXT,                         -- ATS slug, "tenant/board" for workday
  site             TEXT,                         -- workday host
  url              TEXT NOT NULL,                -- canonical URL
  discovered_url   TEXT,
  category         TEXT NOT NULL DEFAULT 'careers'
                   CHECK (category IN ('careers', 'early_careers', 'program', 'aggregator', 'university', 'announcement', 'import')),
  countries        TEXT NOT NULL DEFAULT '[]',   -- JSON list, coverage if known
  access           TEXT NOT NULL DEFAULT 'public' CHECK (access IN ('public', 'login', 'restricted', 'blocked')),
  discovery_method TEXT NOT NULL,                -- seed | ats_probe | homepage_link | posting_url | search | manual | import
  discovery_evidence TEXT,
  enabled          INTEGER NOT NULL DEFAULT 1,
  health           TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (health IN ('unknown', 'ok', 'degraded', 'failing', 'blocked', 'gone', 'disabled')),
  priority         INTEGER NOT NULL DEFAULT 2 CHECK (priority IN (1, 2, 3)),
  interval_hours   REAL,                         -- manual override; NULL = scheduler decides
  next_check_at    TEXT,
  last_attempt_at  TEXT,
  last_success_at  TEXT,
  last_change_at   TEXT,
  last_outcome     TEXT,
  last_error       TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  zero_streak      INTEGER NOT NULL DEFAULT 0,   -- successful checks in a row that returned nothing
  etag             TEXT,
  last_modified    TEXT,
  content_hash     TEXT,
  checks_total     INTEGER NOT NULL DEFAULT 0,
  checks_failed    INTEGER NOT NULL DEFAULT 0,
  postings_total   INTEGER NOT NULL DEFAULT 0,   -- distinct internship postings ever seen (yield)
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  UNIQUE (kind, url)
);
CREATE INDEX sources_employer ON sources(employer_id);
CREATE INDEX sources_due ON sources(enabled, next_check_at);

CREATE TABLE runs (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,                     -- run | collect | enrich | discover | search | audit | research | import
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'failed', 'interrupted')),
  stats       TEXT NOT NULL DEFAULT '{}',
  note        TEXT
);

-- One row per source check. Pruned after CHECK_RETENTION_DAYS.
CREATE TABLE source_checks (
  id           INTEGER PRIMARY KEY,
  source_id    TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  run_id       INTEGER REFERENCES runs(id) ON DELETE SET NULL,
  at           TEXT NOT NULL,
  duration_ms  INTEGER NOT NULL DEFAULT 0,
  outcome      TEXT NOT NULL CHECK (outcome IN ('ok', 'empty', 'not_modified', 'unchanged', 'unavailable', 'blocked',
                 'format_changed', 'invalid', 'pagination', 'rate_limited', 'auth_required', 'gone', 'robots',
                 'deferred')),
  http_status  INTEGER,
  postings     INTEGER,
  new_postings INTEGER,
  error        TEXT
);
CREATE INDEX source_checks_source ON source_checks(source_id, at);

-- A posting as one source reports it (raw observation). Several may map to one opportunity.
CREATE TABLE source_postings (
  id             INTEGER PRIMARY KEY,
  source_id      TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  external_id    TEXT NOT NULL,                  -- ATS requisition/posting id, else the posting URL
  opportunity_id TEXT REFERENCES opportunities(id) ON DELETE SET NULL,
  title          TEXT NOT NULL,
  url            TEXT NOT NULL,
  location_text  TEXT NOT NULL DEFAULT '',
  description    TEXT NOT NULL DEFAULT '',       -- truncated; cleared when long gone (retention)
  content_hash   TEXT NOT NULL,
  published_at   TEXT,                           -- as stated by the source, never our discovery time
  structured     TEXT NOT NULL DEFAULT '{}',     -- JSON: department, employment type, requisition id, ...
  extraction     TEXT NOT NULL DEFAULT 'structured' CHECK (extraction IN ('structured', 'jsonld', 'ai', 'import')),
  present        INTEGER NOT NULL DEFAULT 1,
  miss_count     INTEGER NOT NULL DEFAULT 0,     -- complete successful checks in a row without this posting
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  UNIQUE (source_id, external_id)
);
CREATE INDEX source_postings_opp ON source_postings(opportunity_id);

-- Programs -------------------------------------------------------------------------------------------------------
CREATE TABLE programs (
  id            TEXT PRIMARY KEY,
  employer_id   TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  name_key      TEXT NOT NULL,                   -- employer-scoped identity: type + division + place, no year
  program_type  TEXT NOT NULL,                   -- tracker.taxonomy.PROGRAM_TYPES
  division      TEXT,
  location_text TEXT,
  countries     TEXT NOT NULL DEFAULT '[]',
  eligibility_text TEXT,
  official_url  TEXT,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'discontinued', 'unknown')),
  discovered_via TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE (employer_id, name_key)
);

-- One recruitment window per intake year and kind of evidence. Estimates never overwrite confirmed dates:
-- they are separate rows with kind = 'estimated'.
CREATE TABLE program_windows (
  id           INTEGER PRIMARY KEY,
  program_id   TEXT NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
  intake_year  INTEGER NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('confirmed', 'observed', 'estimated', 'third_party')),
  opens_on     TEXT,
  closes_on    TEXT,
  opens_earliest TEXT,                           -- estimates: plausible range
  opens_latest TEXT,
  source_url   TEXT,
  evidence     TEXT NOT NULL,
  recorded_at  TEXT NOT NULL,
  UNIQUE (program_id, intake_year, kind)
);

-- Opportunities (advertised vacancies only; speculative prospects live in `prospects`) ---------------------------
CREATE TABLE opportunities (
  id              TEXT PRIMARY KEY,              -- sha1(employer_id|canonical_url)[:16]; legacy role ids kept
  employer_id     TEXT NOT NULL REFERENCES employers(id) ON DELETE RESTRICT,
  program_id      TEXT REFERENCES programs(id) ON DELETE SET NULL,
  title           TEXT NOT NULL,
  canonical_url   TEXT NOT NULL,
  title_key       TEXT NOT NULL,
  internship_type TEXT NOT NULL DEFAULT 'unknown',
  intake_year     INTEGER,
  city            TEXT,
  countries       TEXT NOT NULL DEFAULT '[]',
  sectors         TEXT NOT NULL DEFAULT '[]',    -- functional classification of the role itself
  languages       TEXT NOT NULL DEFAULT '[]',
  closes_on       TEXT,
  published_at    TEXT,
  description     TEXT NOT NULL DEFAULT '',
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'open', 'unavailable', 'closed', 'expired', 'unknown')),
  enrichment      TEXT NOT NULL DEFAULT 'pending' CHECK (enrichment IN ('pending', 'done', 'failed', 'skipped')),
  discovered_via  TEXT NOT NULL,                 -- monitoring | search | import | legacy
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  last_verified_at TEXT,
  closed_at       TEXT,
  close_reason    TEXT,
  reopened_count  INTEGER NOT NULL DEFAULT 0,
  review_flag     TEXT,                          -- e.g. "possible_duplicate:<id>"
  excluded_reason TEXT,                          -- not_internship | outside_countries: kept, but not listed
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX opportunities_employer ON opportunities(employer_id);
CREATE INDEX opportunities_status ON opportunities(status);
CREATE UNIQUE INDEX opportunities_url ON opportunities(employer_id, canonical_url);

-- Provenance per material field. The column on `opportunities` mirrors the highest-precedence fact.
CREATE TABLE opportunity_facts (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  field          TEXT NOT NULL,
  method         TEXT NOT NULL CHECK (method IN ('manual', 'structured', 'jsonld', 'rule', 'ai')),
  value          TEXT NOT NULL,                  -- JSON
  source_id      TEXT REFERENCES sources(id) ON DELETE SET NULL,
  model          TEXT,
  prompt_version TEXT,
  evidence       TEXT,                           -- verbatim quote from the source text when available
  recorded_at    TEXT NOT NULL,
  PRIMARY KEY (opportunity_id, field, method)
);

-- Eligibility requirements as stated by the employer, each with its evidence. Evaluated in the browser against the
-- user's private profile (site/eligibility.js).
CREATE TABLE opportunity_requirements (
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL CHECK (kind IN ('work_authorization', 'sponsorship', 'degree_level', 'degree_field',
                   'graduation_year', 'enrollment', 'duration_months', 'start', 'language', 'experience', 'other')),
  value          TEXT NOT NULL,                  -- JSON, shape per kind (docs/eligibility.md)
  method         TEXT NOT NULL CHECK (method IN ('rule', 'ai', 'structured', 'manual')),
  evidence       TEXT NOT NULL,
  recorded_at    TEXT NOT NULL,
  PRIMARY KEY (opportunity_id, kind, value)
);

CREATE TABLE opportunity_events (
  id             INTEGER PRIMARY KEY,
  opportunity_id TEXT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  at             TEXT NOT NULL,
  event          TEXT NOT NULL,                  -- discovered | verified | status | changed | merged | flagged
  from_status    TEXT,
  to_status      TEXT,
  field          TEXT,
  old_value      TEXT,
  new_value      TEXT,
  source_id      TEXT REFERENCES sources(id) ON DELETE SET NULL,
  note           TEXT
);
CREATE INDEX opportunity_events_opp ON opportunity_events(opportunity_id, at);

-- Missed-by-monitoring findings (Workflow C) and benchmark misses (Workflow J).
CREATE TABLE coverage_gaps (
  id             INTEGER PRIMARY KEY,
  employer_id    TEXT REFERENCES employers(id) ON DELETE CASCADE,
  opportunity_id TEXT REFERENCES opportunities(id) ON DELETE CASCADE,
  cause          TEXT NOT NULL CHECK (cause IN ('unknown_employer', 'missing_source', 'broken_connector',
                   'insufficient_search', 'extraction_failure', 'deduplication_error', 'incorrect_classification',
                   'eligibility_filtering', 'restricted_source', 'delayed_detection', 'missed_by_monitoring')),
  detail         TEXT NOT NULL,
  detected_by    TEXT NOT NULL,                  -- search | benchmark
  detected_at    TEXT NOT NULL,
  resolved_at    TEXT,
  resolution     TEXT,
  UNIQUE (employer_id, opportunity_id, cause)
);

-- Opportunity creation ---------------------------------------------------------------------------------------------
CREATE TABLE prospects (
  employer_id   TEXT PRIMARY KEY REFERENCES employers(id) ON DELETE CASCADE,
  status        TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'researched', 'dismissed')),
  score         INTEGER NOT NULL,
  reasons       TEXT NOT NULL,                   -- JSON list of {factor, points, detail}
  research_requested INTEGER NOT NULL DEFAULT 0,
  summary       TEXT,
  application_channel TEXT,
  researched_at TEXT,
  model         TEXT,
  prompt_version TEXT,
  updated_at    TEXT NOT NULL
);

-- Research facts; every one must cite a page that was actually fetched.
CREATE TABLE employer_intel (
  id          INTEGER PRIMARY KEY,
  employer_id TEXT NOT NULL REFERENCES employers(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL CHECK (kind IN ('business', 'teams', 'recruiting', 'policy', 'development', 'channel', 'contact_route')),
  text        TEXT NOT NULL,
  evidence    TEXT NOT NULL,
  source_url  TEXT NOT NULL,
  method      TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE (employer_id, kind, text)
);

-- Operations -------------------------------------------------------------------------------------------------------
-- Durable work queue. A job is identified by (kind, key) so enqueueing twice is a no-op.
CREATE TABLE jobs (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL,
  key             TEXT NOT NULL,
  payload         TEXT NOT NULL DEFAULT '{}',
  status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'dead')),
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 5,
  next_attempt_at TEXT NOT NULL,
  lease_until     TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (kind, key)
);
CREATE INDEX jobs_due ON jobs(status, next_attempt_at);

CREATE TABLE ai_calls (
  id             INTEGER PRIMARY KEY,
  at             TEXT NOT NULL,
  task           TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model          TEXT NOT NULL,
  input_hash     TEXT NOT NULL,
  input_tokens   INTEGER NOT NULL DEFAULT 0,
  output_tokens  INTEGER NOT NULL DEFAULT 0,
  search_calls   INTEGER NOT NULL DEFAULT 0,
  cost_usd       REAL NOT NULL DEFAULT 0,
  outcome        TEXT NOT NULL CHECK (outcome IN ('ok', 'cached', 'invalid', 'refused', 'transient', 'budget', 'error')),
  error          TEXT
);
CREATE INDEX ai_calls_at ON ai_calls(at);

CREATE TABLE ai_cache (
  task           TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_hash     TEXT NOT NULL,
  output         TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (task, prompt_version, input_hash)
);

CREATE TABLE search_queries (
  id               INTEGER PRIMARY KEY,
  provider         TEXT NOT NULL,
  query            TEXT NOT NULL,
  facet            TEXT NOT NULL,                 -- JSON: {sector, country, type, lang}
  runs             INTEGER NOT NULL DEFAULT 0,
  results_total    INTEGER NOT NULL DEFAULT 0,
  new_opportunities INTEGER NOT NULL DEFAULT 0,
  new_employers    INTEGER NOT NULL DEFAULT 0,
  last_run_at      TEXT,
  next_run_at      TEXT,
  UNIQUE (provider, query)
);

CREATE TABLE notifications (
  id             INTEGER PRIMARY KEY,
  key            TEXT NOT NULL UNIQUE,           -- dedup key, e.g. "new:<opportunity id>"
  kind           TEXT NOT NULL,
  priority       TEXT NOT NULL CHECK (priority IN ('urgent', 'normal', 'info')),
  title          TEXT NOT NULL,
  body           TEXT NOT NULL DEFAULT '',
  opportunity_id TEXT REFERENCES opportunities(id) ON DELETE CASCADE,
  employer_id    TEXT REFERENCES employers(id) ON DELETE CASCADE,
  program_id     TEXT REFERENCES programs(id) ON DELETE CASCADE,
  source_id      TEXT REFERENCES sources(id) ON DELETE CASCADE,
  created_at     TEXT NOT NULL,
  digested_at    TEXT
);

CREATE TABLE audit_events (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL,
  actor     TEXT NOT NULL,                       -- system | user | migration
  action    TEXT NOT NULL,
  entity    TEXT NOT NULL,
  entity_id TEXT,
  detail    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE coverage_evaluations (
  id                INTEGER PRIMARY KEY,
  at                TEXT NOT NULL,
  benchmark_version TEXT NOT NULL,
  metrics           TEXT NOT NULL,               -- JSON
  misses            TEXT NOT NULL                -- JSON
);
