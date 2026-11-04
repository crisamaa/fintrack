# Source connectors

Every connector implements three methods (`tracker/connectors/__init__.py`):

| Method | Does | Network |
| --- | --- | --- |
| `detect(url) -> SourceRef or None` | recognise a board URL of this platform | no |
| `validate(ref, http) -> str or None` | confirm the board exists; its display name if the API gives one | yes |
| `fetch(source, http) -> FetchResult` | read postings, internships only (`titles.looks_like_internship`) | yes |

`FetchResult.complete` must be `False` unless every page was read, because only complete reads may close postings.
Raise `SourceFailure(kind, …)` for anything else: `gone`, `blocked`, `auth_required`, `rate_limited`, `unavailable`,
`invalid`, `format_changed`, `pagination`, `robots`, `unsafe_url`. Never return an empty list for a failure.

## Supported

| Kind | API | Verified live |
| --- | --- | --- |
| greenhouse | Job Board API `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | v1, 2026-11-04 |
| lever | Postings API `api.lever.co/v0/postings/{slug}` (EU: `api.eu.lever.co`) | v1, 2026-11-04 (global only) |
| ashby | Posting API `api.ashbyhq.com/posting-api/job-board/{slug}` | v1, 2026-11-04 |
| workday | `/wday/cxs/{tenant}/{board}/jobs` — the endpoint Workday career sites use; not formally documented | v1, 2026-11-04 |
| smartrecruiters | Posting API `api.smartrecruiters.com/v1/companies/{id}/postings` | **no** — fixture from docs |
| recruitee | Careers Site API `{slug}.recruitee.com/api/offers/` | **no** — fixture from docs |
| personio | XML feed `{slug}.jobs.personio.de/xml` | **no** — fixture from docs |
| page | any careers page: conditional GET, text hash, schema.org JobPosting JSON-LD, links to ATS boards, else AI | v1 for HTML; JSON-LD from fixture |

"Verified live" means a request against a real board succeeded at that date. The three marked **no** are tested
only against fixtures built from the platforms' documentation, because this build environment could not reach
them; the first scheduled run will show their real health in the Sources view.

## Adding a connector

1. Create a class in `tracker/connectors/ats.py` (or a new module) with `kind`, `detect`, `validate`, `fetch`.
   Wrap `fetch` in `@_shape` so unexpected JSON becomes `format_changed`.
2. Register it in `_registry()` in `connectors/__init__.py`; add its URL to `seeds.firm_source_ref` and, if boards can
   be guessed from a firm name, to `work.PROBE_KINDS` / `_BOARD_URL`.
3. Add a saved fixture and contract tests in `tests/test_connectors.py`: internships only, pagination, every
   failure kind, detection of its URLs.

Nothing else changes: the scheduler, ingestion and site treat every connector the same way.

## Restrictions

The tracker obeys robots.txt, waits at least one second between requests to a host, identifies itself with a
generic user agent and never logs in. Firms that refuse automated visits are in `firms/blocked.yaml`; they are
shown as "check by hand" and as outreach prospects instead. University portals, LinkedIn and email newsletters are
not scraped: add a posting from them with **Import a lead** (workflow input or `python -m tracker import-lead URL`).
Before opening the site to other people, re-check each source's terms: personal use and publication differ.
