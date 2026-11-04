# Implementation record — v2 overhaul (2026-11-05)

Keep this file current across sessions: tick what is done, and say plainly what is partial or blocked.

## What v1 was

A daily GitHub Actions job read 247 firms (Greenhouse, Lever, Ashby, Workday, careers pages via Gemini), enriched new
postings with Gemini, merged them into `data/roles.json`, and a static site (Discover, Pipeline, Deadlines; three
themes; keyboard model) showed them with application tracking in localStorage. A monthly job added FINMA banks with
readable boards. 224 tests passed.

## Preservation report

| Part | Decision | Why |
| --- | --- | --- |
| Static site design, themes, layout, keyboard model, table/detail patterns | **Retained** | Already well-made; new views reuse its pieces |
| Pipeline and Deadlines views, application tracking (`fintrack.tracker.v1`) | **Retained** (data untouched) | Works; ids are preserved, so tracked applications still resolve |
| GitHub Actions + Pages hosting | **Retained** | Free, no server to secure, fits a single user |
| Greenhouse/Lever/Ashby/Workday readers (`feeds.py`) | **Refactored** into `connectors/ats.py` | Behind a connector interface with typed failures; EU Lever, pagination failures |
| Polite fetching (`fetch.py`) | **Refactored** into `http.py` | Kept robots, delay, browser fallback, link-preserving text; added SSRF guard, size cap, conditional GET |
| Gemini client and budget (`ai.py`, `budget.py`) | **Refactored** | Provider interface, cache, versioned prompts, per-call records in the DB, daily limits, quote validation |
| Firm list and name matching (`firms.py`) | **Refactored** into `firms.py` (seed loader) + `names.py` | Same YAML format; now a seed that discovery builds on |
| FINMA parsing (`discover.py`) | **Refactored** into `discovery.py` | Same parser; names now become verified candidates |
| `data/roles.json`, `firm_state.json` (JSON as database) | **Replaced** by SQLite + JSONL dump; files moved to `data/legacy/` and imported once | Could not represent sources, programs, provenance, history or jobs |
| `daily.py` loop (every firm, once a day, AI-gated) | **Replaced** by `run.py` + `schedule.py` + `jobs.py` + `work.py` | Per-source schedules; AI work queued so collection never waits on it |
| `merge.py` | **Replaced** by `ingest.py`, `identity.py`, `lifecycle.py`, `facts.py` | Cross-source dedup, explicit lifecycle, provenance |
| `models.py` | **Removed** | Replaced by the schema and `prompts.py` models |
| `monthly-discover.yml`, `daily.yml` | **Replaced** by `run.yml` | One scheduled workflow; cadence decided inside |
| `site/data/roles.json` | **Removed** (generated) | Replaced by `opportunities.json`, `market.json`, `ops.json`, `digest.json` |

## Migration

- `tracker/migrations/0001_schema.sql` creates the v2 schema; `tracker/legacy.py` imports v1 once: role ids become
  opportunity ids, v1 AI-filled fields are recorded as AI facts, firm state becomes source state; it fails as a whole
  unless every v1 role is accounted for. v1 had 0 roles and 247 firm states at import; all 247 imported.
- Browser data: v1 keys are read unchanged; new data uses new keys; export v2 includes everything, v1 files import.

## Definition-of-done workflows

| | Workflow | Status | Tests |
| --- | --- | --- | --- |
| A | New employer discovery | done | `test_work.py::test_unfamiliar_firm_is_verified_registered_and_its_sources_found` and identity/classification guards |
| B | New vacancy detection → site with evidence | done | `test_ingest.py` (Workflow B section), `test_run.py::test_full_run…` |
| C | Cross-source discovery → coverage gap → source discovery | done | `test_ingest.py::test_search_find_for_monitored_employer…`, `test_search.py` |
| D | Recurring program monitoring | done | `test_work.py::test_program_history…`, `…confirmed_dates_need_a_quote…`, `filters.test.mjs` |
| E | Eligibility with evidence and uncertainty | done (browser) | `site/eligibility.test.mjs` |
| F | Application tracking with reminders | done (v1 kept; Deadlines extended) | `site/filters.test.mjs` |
| G | Speculative prospecting, research, outreach | done; drafts are templates, never sent | `test_work.py::test_prospect…`, `eligibility.test.mjs` (drafts) |
| H | Source failure and recovery | done | `test_ingest.py` (Workflow H section) |
| I | AI unavailable or over budget | done | `test_work.py` (Workflow I section), `test_ai.py`, `test_run.py::test_run_without_ai_key…` |
| J | Coverage audit | done (needs a filled benchmark to be meaningful) | `test_work.py::test_audit…` |

Live verification is pending for everything that talks to the web; see `docs/limitations.md`.

## Checklist

- [x] Phase 0 — audit, test baseline (224 passing), plan
- [x] Phase 1 — schema, migrations, JSONL persistence, integrity checks, backup/restore, legacy import
- [x] Phase 2 — connector interface; 7 ATS connectors + careers pages (JSON-LD, AI); typed failures; dedup;
      lifecycle; provenance; per-source scheduling; job queue; AI decoupled from collection
- [x] Phase 3 — candidates from FINMA, postings, search, seeds; identity verification; AI classification with quote
      check; source discovery (ATS probes, bounded careers-link crawl); subsidiaries kept separate
- [~] Phase 3 — more directories (ESMA, FCA, SMV/SBS): **not done**, no verified machine-readable source
- [x] Phase 4 — programs and windows (confirmed/observed/estimated/third-party), schedule boost before openings,
      lead import (URL) for newsletters and university sources
- [~] Phase 4 — email/newsletter ingestion: **not automated** (URL import only)
- [x] Phase 5 — requirement extraction (rules + quoted AI); browser eligibility engine; explainable ranking
- [x] Phase 6 — prospects with reasons; grounded research; local CRM; drafts; follow-ups
- [x] Phase 7 — Discover/Programs/Firms/Outreach/Pipeline/Deadlines/Sources/Profile in the existing design;
      checked in Chromium at 1440 px and 390 px (no console errors, no horizontal scroll)
- [x] Phase 8 — workflows, docs, backup/restore tested
- [ ] Live run against real sources (blocked here: no outbound network) — first scheduled run
- [ ] Benchmark filled to ~100 employers with verified postings (needs a person)
