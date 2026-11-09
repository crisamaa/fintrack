-- Role-level classification of each opportunity: what the intern will do, independent of the employer's sector.
-- One row per opportunity, recomputed only when the title, department or description change (content_hash) or the
-- classifier version changes. Interpretation only: the raw title and description stay on opportunities.
CREATE TABLE opportunity_roles (
  opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id) ON DELETE CASCADE,
  function       TEXT NOT NULL,                  -- tracker.roles.FUNCTIONS id, or unknown
  functions      TEXT NOT NULL DEFAULT '[]',     -- JSON list, primary first, then secondary functions
  confidence     TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low', 'none')),
  method         TEXT NOT NULL CHECK (method IN ('title', 'department', 'description', 'ai', 'none')),
  evidence       TEXT,                           -- the matched words, the department, or the AI quote
  department     TEXT,                           -- as the job board stated it, kept so reclassification can reuse it
  content_hash   TEXT NOT NULL,
  version        TEXT NOT NULL,                  -- classifier version
  model          TEXT,                           -- for AI classifications
  classified_at  TEXT NOT NULL,
  changed_at     TEXT NOT NULL                   -- when the primary function last changed
);
CREATE INDEX opportunity_roles_function ON opportunity_roles(function);
