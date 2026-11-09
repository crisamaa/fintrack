# Personalized Discover — implementation plan

Spec: the "Personalized discovery & intelligent review overhaul" brief (given in the session of 2026-11-09). This plan
maps it onto the repository as it is.

## Audit findings that shape the design

- Two halves, no server: `tracker/` (Python, SQLite persisted as JSONL, runs on Actions) writes `site/data/*.json`;
  `site/` is static ES modules. **Everything personal lives in the browser's localStorage** (`site/store.js`), by
  design and by CLAUDE.md rule. So profiles, decisions, events and suggestions are browser data; matching runs in the
  browser over cached, tracker-computed classifications. No API server is added (spec 13 says use existing conventions).
- Role classification today: `taxonomy.classify_sectors(title + department)` — only 65 of 301 open postings get a
  sector; `toRole` then falls back to the **employer's** sectors. That is exactly the "classify by firm" defect.
- Fit today: `site/relevance.js` adds named points; eligibility is `site/eligibility.js` (`assess`) — reused as-is,
  kept as a separate dimension.
- Save = star = global pipeline stage "Shortlisted" (`fintrack.tracker.v1`). Dismiss = global set
  (`fintrack.dismissed.v1`). Preferences = `profile.prefs` {sectors, countries, types}.
- Change history per opportunity exists (`opportunity_events`, `changed` per field) and `reopened` count.

## Architecture

1. **Role intelligence (tracker)** — `tracker/roles.py`: a function taxonomy (finance functions reuse `SECTORS` keys;
   adds non-finance functions: technology, data/quant, operations, compliance/legal, HR, marketing, sales, audit,
   ESG, investor relations, strategy, real estate). Layered: title rules (high), department (medium), description
   keyword evidence (low), AI (enrichment adds `role_function` + verbatim quote, used only when rules are low/none),
   else unknown. Stored in new table `opportunity_roles` (migration 0002) keyed by opportunity, with content hash and
   classifier version so it is recomputed only when title/description/department change or the classifier version
   changes. New run step `classify` (before export). Export adds `role` per opportunity and per program, and the
   taxonomy.
2. **Targets store (site)** — `site/targets.js`, key `fintrack.targets.v1`: profiles (prefs, versions, revisions),
   per-profile decisions, an append-only capped event log, per-profile suggestions, visit marks, global role
   corrections. Applications stay in the existing tracker store (one record per real-world posting; `origin_target`
   recorded).
3. **Matching engine** — `site/match.js`, pure: hard constraints first, then weighted relevance (role 40, geography 20,
   timing/format 15, employer 10, other 10, bounded behaviour 5), neutral for unknowns, uncertainty codes, category
   (top/good/adjacent/review/low/excluded), reason lines. Versioned `MATCH_VERSION`.
4. **Feed** — `site/feed.js`, pure: one evaluation, section views (Top, New, Closing, Review, Adjacent, Dismissed,
   Opening soon), resurfacing by material fingerprint.
5. **Learning** — `site/learn.js`, pure: interpretable pattern detection over the active target's events; suggestions
   need approval; cooldowns; revisions with undo.
6. **UI** — Discover gets "For you" (default) and "All opportunities" (the existing table, untouched). For you = list
   in the view + the existing detail pane as the right pane. Onboarding/editor, target switcher, decision actions,
   undo toast, keyboard.

## Tasks

1. `tracker/roles.py` classifier + tests (finance fixtures; SWE at fintech is technology).
2. Migration 0002, `classify` step, AI layer in enrichment, export of `role`/taxonomy + tests; pytest checks the site's
   taxonomy copy matches.
3. `site/match.js` + tests (spec 17.2).
4. `site/targets.js` store + tests (spec 17.1, legacy migration).
5. `site/feed.js` sections + resurfacing + tests (17.3, 17.6).
6. `site/learn.js` + tests (17.5).
7. UI integration in `app.js`, `index.html`, `style.css`.
8. Browser scenarios A–K with Playwright against a locally exported data copy; docs.

Verification per task: `.venv/bin/python -m pytest && .venv/bin/python -m pyflakes tracker tests && node --test site/*.test.mjs`.
