# Architecture

Fintrack is a modular monolith in two halves that never share a process:

- **The tracker** (`tracker/`, Python 3.12): a scheduled batch job on GitHub Actions. It owns all market data in a
  SQLite database, persisted as one JSONL file per table in `data/db/`, and writes the site's data files.
- **The site** (`site/`, plain ES modules, no build step): a static page on GitHub Pages. It reads the data files
  and keeps everything personal — your eligibility profile, applications, contacts, drafts — in the browser's
  `localStorage`.

Why this shape: it costs nothing to run, needs no server to secure, and the repository is public, so personal data
must never reach it. A server with Postgres and a worker would add hosting cost and an attack surface without
solving a problem this single-user tool has. The database layer is plain SQL over `sqlite3`, so moving to a server
database later is a contained change (see *Known limitations*).

## Data flow

```
 seed files (firms/*.yaml) ─┐
 directories (FINMA)       ─┼─> employer candidates ─> discovery (verify identity, classify) ─> employers
 search results, postings  ─┘                                                                    │
                                                                                                 v
                    sources (registry, one employer can have many) <── source discovery (ATS probes, careers links)
                                  │  scheduler: next_check_at per source
                                  v
             connector.fetch ──> ingest: raw posting -> match/dedup -> opportunity + facts + requirements
                                  │            │                          │
                                  │            └─> programs + windows     └─> jobs: enrich / extract_page / verify (AI)
                                  v
             notifications, coverage gaps, prospects, coverage audit ──> export: site/data/*.json ──> the site
```

## Modules

| Concern | Module |
| --- | --- |
| Database, migrations, JSONL persistence, backup/restore | `db.py`, `migrations/` |
| Seed files, v1 import | `seeds.py`, `legacy.py`, `firms.py` |
| Employer identity, aliases, subsidiaries, candidates, source registry | `entities.py`, `names.py` |
| Employer discovery (directories, candidate verification) | `discovery.py` |
| Source connectors | `connectors/` (`ats.py`, `page.py`) |
| Safe HTTP (SSRF guard, robots, politeness, size caps, conditional GET) | `http.py` |
| Ingestion, absence handling, coverage-gap detection | `ingest.py` |
| Opportunity identity and dedup | `identity.py` |
| Lifecycle state machine | `lifecycle.py` |
| Field provenance and precedence | `facts.py` |
| Requirement extraction (rules) | `requirements.py`, `taxonomy.py` |
| Recurring programs and windows | `programs.py` |
| Scheduling per source | `schedule.py` |
| Durable job queue | `jobs.py`, handlers in `work.py` |
| AI provider, service, budget, prompts | `ai.py`, `budget.py`, `prompts.py` |
| Web search channel | `search.py` |
| Prospects and research | `prospects.py` |
| Coverage audit | `coverage.py` |
| Notifications, digest | `notify.py` |
| Site data export | `export.py` |
| Orchestration, CLI | `run.py`, `__main__.py` |
| Site: views, eligibility, ranking, outreach drafts, local store | `site/app.js`, `eligibility.js`, `relevance.js`, `outreach.js`, `store.js`, `filters.js` |

## Database

`tracker/migrations/0001_schema.sql` is the schema, commented table by table. In short:

- **Employers**: `employers` (with `parent_id` for subsidiaries, `access_note` for firms that refuse automated
  visits), `employer_aliases`, `employer_sectors` (overlapping taxonomy), `employer_locations`, `employer_candidates`.
- **Sources**: `sources` (kind, board, canonical and discovered URL, category, access, discovery method and
  evidence, health, schedule, ETag/hash, counters and yield), `source_checks` (one row per check, 45 days),
  `source_postings` (each posting as a source reports it — the raw observation), `runs`.
- **Recruitment**: `opportunities` (advertised vacancies only), `opportunity_facts` (provenance per field),
  `opportunity_requirements` (stated eligibility conditions with evidence), `opportunity_events` (lifecycle and
  changes), `programs`, `program_windows`, `coverage_gaps`.
- **Opportunity creation**: `prospects`, `employer_intel` (every note cites a fetched page).
- **Operations**: `jobs`, `ai_calls`, `ai_cache`, `search_queries`, `notifications`, `audit_events`,
  `coverage_evaluations`, `meta`.
- **Personal data** is not in the database: it is in the browser (`site/store.js`).

Rules: foreign keys and `PRAGMA integrity_check` are verified on every load and before every save (a failing check
writes nothing); upserts are idempotent; related updates run in one transaction; history is never deleted except
by the retention rules in `run.prune` (old check rows, AI cache, digested notifications, long-gone posting text).

**Migrations** are numbered SQL files. `db.load` replays a dump at its recorded schema version, snapshots the dump
to `backups/` when newer migrations are pending, then applies them. To add one: create `0002_<name>.sql` (no `--`
or `;` inside string literals), add a test in `tests/test_db.py`, run `python -m tracker migrate`.

## Opportunity lifecycle

`pending` (found outside the employer's own sources) → `open` (listed by an employer-controlled source) →
`closed` (a complete, successful check no longer lists it) or `expired` (stated closing date passed);
`unavailable` when every source has failed for 7 days — never closed for that; reopening is a counted transition.
Allowed transitions are explicit in `lifecycle.py`; each writes an event with its reason.

Closing needs evidence: only a `complete` check (every page read, no failure) can mark a posting absent; an empty
board must be empty three checks in a row; AI-extracted postings must be missing twice.

## Deduplication

In order: same posting at the same source; same canonical URL (tracking parameters removed); same requisition id;
same normalised title with compatible place and intake year at another source. Similar-but-different titles are
never merged; the new record is flagged `possible_duplicate:<id>`.

## Provenance

Every material field is a fact with its method, source, model, prompt version and evidence. Precedence:
manual > structured (ATS API) > JSON-LD > rule > AI. An AI value can fill a gap but never overwrite better data, and
an AI fact is stored only if its quote occurs in the source text.

## Scheduling

GitHub Actions starts a run six times a day. Inside, each source has its own `next_check_at`
(`schedule.py`): priority or active sources every 8 h, normal daily, low-activity every 3 days, every 6 h when a
program at that employer is expected to open; failures back off exponentially (2 h … 72 h, blocked sources weekly),
with deterministic jitter. Discovery directories run every 28 days, the coverage audit weekly or when the
benchmark file changes, the digest at 05:00 UTC.

Jobs (`jobs.py`) are keyed by (kind, key) so producers are idempotent; a claimed job holds a 30-minute lease that the
next run recovers if a run dies; failures retry with exponential backoff and become `dead` after 5 attempts.
Budget stops and AI outages *defer* jobs without spending an attempt. One run holds a lock in `meta` and the
workflow has a concurrency group, so runs never overlap.

## Gemini

Gemini is used only where language understanding adds value: reading careers pages without structured data,
enriching postings (countries, type, deadline with its quote, requirements with quotes), classifying unfamiliar
employers from their own homepage, reading program pages for stated dates, researching prospects, and (optional)
grounded web search. It is never used for ATS APIs, IDs, unchanged content, scheduling or filtering.

`ai.py` has a small `Provider` interface (one method) and `GeminiProvider`; swapping providers means one new class.
`AIService` adds the response cache (keyed by task, prompt version and input hash), the budget check, structured
output validation, and one `ai_calls` row per call — written outside job transactions so billed usage survives a
rollback. Prompts and their schemas are versioned in `prompts.py`. Limits: optional monthly dollar cap, daily
request limit, daily search limit (`docs/configuration.md`).

## Security

- Secrets exist only as GitHub Actions secrets, read in `config.py`; the Gemini key goes in a header, never a URL.
  The site contains no credentials.
- `http.check_url` allows only http(s) on default ports without credentials, refuses internal hostnames and any
  host resolving to a non-public address, and is re-applied on every redirect hop and to every request a headless
  browser makes. Responses are capped at 5 MB and time-limited.
- All web content is untrusted: prompts say so, outputs are schema- and quote-validated, the site renders text with
  `textContent` (the `h()` helper) and links only through `safeUrl` (http/https only).
- The tracker never logs in, solves CAPTCHAs, or bypasses robots.txt; restricted sources are added by hand as leads.
- The site never sends messages or applications; drafts are copied or opened in the user's own mail client.
- Workflow inputs reach shell commands through environment variables, not template interpolation.
