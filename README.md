# Fintrack

A personal finance-internship intelligence tool. It discovers financial employers and their recruitment sources,
monitors them on individual schedules, records every internship with where each fact came from, tracks recurring
programs and their recruitment windows, assesses your eligibility in your own browser, and supports applications
and outreach to firms that advertise nothing.

- **Tracker** (`tracker/`, Python): runs on GitHub Actions six times a day; market data in a SQLite database kept as
  JSONL in `data/db/`.
- **Site** (`site/`, static): GitHub Pages. Your profile, applications and contacts stay in your browser.

## Workspaces

| View | What it is for |
| --- | --- |
| Discover | every internship found, with eligibility, fit ranking, sources and evidence |
| Programs | recurring programs: confirmed, observed and estimated recruitment windows |
| Firms | every tracked employer: sectors, offices, sources and their health, openings, programs, prospect research |
| Outreach | firms worth approaching directly; contacts, interactions, follow-ups, message drafts (never sent for you) |
| Pipeline | your applications by stage |
| Deadlines | closing dates, your next steps, outreach follow-ups, program openings |
| Sources | source health, AI usage and spend, search, discovery, coverage metrics and gaps |
| Profile | your eligibility profile and preferences (private, in this browser) |

## Setup (owner)

1. Settings → Pages: source **GitHub Actions**.
2. Create a Gemini API key (aistudio.google.com) and add it as the repository secret `GEMINI_API_KEY`. Before using a
   billed key, add the variable `AI_MONTHLY_CAP_USD` (e.g. `5`). See [docs/configuration.md](docs/configuration.md).
3. Actions → **Tracker run** → Run workflow. The site is then at `https://<user>.github.io/<repository>/`.
4. Open **Profile** on the site and enter your work rights (tick Confirmed only for what you have verified),
   degree, graduation year and languages.

Adding a firm by hand: add it to `firms/firms.yaml` (format unchanged from v1; optional `sectors`, `website`,
`priority`, `enabled`), or a name and website to `firms/candidates.yaml` for discovery to verify.

## Development

```bash
python -m pip install -e ".[dev]"
```

```bash
pytest && python -m pyflakes tracker tests && node --test site/*.test.mjs
```

## Documentation

- [Architecture](docs/architecture.md): modules, database, lifecycle, dedup, provenance, scheduling, Gemini, security
- [Source connectors](docs/connectors.md): supported platforms, adding one, access restrictions
- [Eligibility and ranking](docs/eligibility.md)
- [Coverage measurement](docs/coverage.md)
- [Configuration and costs](docs/configuration.md)
- [Operations](docs/operations.md): running, monitoring, AI spend, backup, restore, rollback
- [Known limitations](docs/limitations.md)
- [Implementation record](docs/IMPLEMENTATION.md): what was kept, changed and removed in the v2 overhaul, and what
  remains
