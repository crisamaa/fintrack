# Europe finance internship tracker

A small website that lists finance internships (banking, private equity, finance, fintech) in Switzerland and the
EU/EEA. A Python job reads firms' job boards and careers pages every day, asks a small AI model (Claude Haiku) to
pick out internships and fill in country, season, closing date and languages, and writes the result to a static
site on GitHub Pages. There is no server and no database: the data lives in the repository as JSON.

## Setup (done by the owner)

These steps involve accounts and keys, so you do them yourself.

1. Create a GitHub repository (public, so Pages and Actions are free).
2. Push the `feat/europe-internship-tracker` branch and merge it into the default branch.
3. In the repository, go to Settings > Pages and set the source to **GitHub Actions**.
4. Create an Anthropic API account at console.anthropic.com. Buy prepaid credit and keep **auto-reload off**, so
   spending can never go beyond what you paid in.
5. Create an API key and add it as a repository secret named `ANTHROPIC_API_KEY` (Settings > Secrets and
   variables > Actions > New repository secret).
6. Open the Actions tab, choose **Daily run**, and click **Run workflow**. When it finishes, the site is at
   `https://<your-user>.github.io/<repository>/`.

## What runs when

| Workflow | When | What it does |
| --- | --- | --- |
| Daily run | 05:00 UTC every day, or by hand | Reads all firms, enriches new postings, commits `data/` and `site/data/` if they changed, deploys `site/` to Pages. If the run fails, data is still committed, the site is still deployed, and the workflow is marked failed. |
| Monthly discovery | 04:00 UTC on the 1st, or by hand | Reads FINMA's list of authorised banks, looks for job boards we can read, and commits changes to `firms/`. |
| Tests | On every push | `pytest` and `node --test "site/*.test.mjs"`. |

Only one daily run goes at a time. A full first pass over every firm can take most of the one-hour job limit; if
it does not finish, the data saved so far is committed and the next run carries on (unchanged pages are skipped).

## What it costs

- GitHub Actions and Pages: free for a public repository.
- Anthropic API: Claude Haiku at $0.10 per million input tokens and $0.50 per million output tokens. A spending cap is built in:
  `MONTHLY_CAP_USD = 5.00` in `tracker/budget.py`, and no new AI call starts once $4.90 is spent that month.
  Spending is tracked in `data/spend.json`. Pages that have not changed since the last run are not sent to the AI
  again, so after the first pass a normal month should cost a small fraction of the cap. Compare `data/spend.json`
  with the Anthropic console now and then.
- The model is a single constant, `MODEL` in `tracker/ai.py`. Changing the model is a one-line edit; update the
  prices in `tracker/budget.py` to match.

## Adding a firm

Edit `firms/firms.yaml` and add an entry:

```yaml
- id: example-bank          # unique, lower-case, stable
  name: Example Bank
  sector: banking           # banking | private_equity | finance | fintech
  country: CH               # home country, ISO code
  source: greenhouse        # greenhouse | lever | ashby | workday | page
  slug: example-bank        # board name for greenhouse/lever/ashby; "tenant/board" plus `site` for workday
```

For a firm with no readable board, use `source: page` and `careers_url: https://...` instead of `slug`. Check
the site's terms and `robots.txt` allow the visit first; the tracker also obeys `robots.txt` itself. Run
`pytest` before pushing.

Firms that refuse automated visits go in `firms/blocked.yaml` (a plain list of names). They are not read; the
site lists them under "Not covered" so visitors know to check them by hand.

## Known limits

- **Firms not covered** (`firms_not_covered` in the site data): some firms block automated visits, for example
  UBS, EFG, BNP Paribas and others in `firms/blocked.yaml`. Their internships are missing and must be checked by hand.
- **Unposted roles**: many internships are never posted publicly or are filled through campus or recruiter
  channels. The tracker only sees what firms publish on their own boards and careers pages.
- **Occasional AI errors**: the AI can misread a page, miss a posting, or get a date, country or season wrong.
  Postings it fails on repeatedly are retried less often. Always open the original ad before applying.
- Careers pages are read as text; heavy JavaScript pages use a headless browser fallback and may still come up empty.

## Before opening the site to others

Re-check the terms of use and `robots.txt` of the firms and boards listed, and the Anthropic and GitHub terms,
before advertising the site or letting other people rely on it. Permission to read a page for personal use does
not automatically extend to a public service.

## Development

```
python -m pip install -e ".[dev]"
pytest
node --test "site/*.test.mjs"
python -m tracker.daily      # needs ANTHROPIC_API_KEY; makes real AI calls
```
