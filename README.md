# Fintrack

A personal finance-internship intelligence tool. It discovers financial employers and their recruitment sources,
monitors them on individual schedules, records every internship with where each fact came from and what the role
actually involves, tracks recurring programs and their recruitment windows, ranks internships against your own career
targets and assesses your eligibility in your own browser, and supports applications and outreach to firms that
advertise nothing.

- **Tracker** (`tracker/`, Python): runs on GitHub Actions six times a day; market data in a SQLite database kept as
  JSONL in `data/db/`.
- **Site** (`site/`, static): GitHub Pages. Your profile, career targets, decisions, applications and contacts stay in
  your browser.

## Workspaces

| View | What it is for |
| --- | --- |
| Discover › For you | the default: your active career target's feed (Top matches, Newly found, Closing soon, Needs review), reviewed in a list beside the details; save, dismiss, mark applied |
| Discover › All opportunities | every internship found, with all filters, eligibility, fit ranking, sources and evidence |
| Programs | recurring programs: confirmed, observed and estimated recruitment windows |
| Firms | every tracked employer: sectors, offices, sources and their health, openings, programs, prospect research |
| Outreach | firms worth approaching directly; contacts, interactions, follow-ups, message drafts (never sent for you) |
| Pipeline | your applications by stage |
| Deadlines | closing dates, your next steps, outreach follow-ups, program openings |
| Sources | source health, AI usage and spend, search, discovery, coverage metrics and gaps |
| Profile | your eligibility profile (work rights, degree, languages), shared by every target; private, in this browser |

## Setup (owner)

1. Settings → Pages: source **GitHub Actions**.
2. Create a Gemini API key (aistudio.google.com) and add it as the repository secret `GEMINI_API_KEY`. Before using a
   billed key, add the variable `AI_MONTHLY_CAP_USD` (e.g. `5`). See [docs/configuration.md](docs/configuration.md).
3. Actions → **Tracker run** → Run workflow. The site is then at `https://<user>.github.io/<repository>/`.
4. Open **Profile** on the site and enter your work rights (tick Confirmed only for what you have verified),
   degree, graduation year and languages.
5. Open **Discover** and set up your first career target (four short steps: name, the functions you want, optional
   places / timing / employers, review).

## Career targets

A career target is one search strategy, e.g. "Investment Banking — Switzerland" or "Asset Management — Europe". Each has
its own preferences, feed, decisions and suggestions; dismissing a posting in one target never hides it in another,
while applications are shared (one Pipeline record per posting). Switch targets from the menu at the top of Discover.

- **Ranking** uses the role each internship actually involves (classified by the tracker from the title, department
  and description, with Gemini only for unclear cases), not the employer's sector, so a bank's software internship
  does not rank as investment banking. Every posting shows why it ranks where it does; there are no percentages.
- **Requirements** you mark as such (only these countries, formats or intake years) remove postings that clearly fail
  them; missing information never does, it goes to Needs review instead.
- **Learning:** after consistent, explained dismissals or saves, Fintrack suggests a preference change for that
  target. Nothing changes until you approve it, and every change can be undone from Edit target.

Nothing is ever removed from All opportunities. Details: [docs/personalization.md](docs/personalization.md).

Adding a firm by hand: add it to `firms/firms.yaml` (format unchanged from v1; optional `sectors`, `website`,
`priority`, `enabled`), or a name and website to `firms/candidates.yaml` for discovery to verify.

## Development

```bash
python -m pip install -e ".[dev]"
```

```bash
pytest && python -m pyflakes tracker tests && node --test site/*.test.mjs
```

To try the site with fresh role classifications before the next tracker run, and to walk through the Discover
scenarios in a headless browser (manual, needs `python -m playwright install chromium`), see
`tests/e2e/discover_scenarios.py`. Neither touches `data/db/` or `site/data/`.

## Documentation

- [Architecture](docs/architecture.md): modules, database, lifecycle, dedup, provenance, scheduling, Gemini, security
- [Source connectors](docs/connectors.md): supported platforms, adding one, access restrictions
- [Eligibility and ranking](docs/eligibility.md)
- [Personalised Discover](docs/personalization.md): career targets, role classification, matching, feed sections,
  learning from decisions
- [Coverage measurement](docs/coverage.md)
- [Configuration and costs](docs/configuration.md)
- [Operations](docs/operations.md): running, monitoring, AI spend, backup, restore, rollback
- [Known limitations](docs/limitations.md)
- [Implementation record](docs/IMPLEMENTATION.md): what was kept, changed and removed in the v2 overhaul, and what
  remains
