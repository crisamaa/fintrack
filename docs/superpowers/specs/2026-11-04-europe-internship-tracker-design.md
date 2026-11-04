# Europe Finance Internship Tracker: Design

Date: 2026-11-04. Status: awaiting Mattia's review.

## Why

Mattia holds Swiss and Peruvian passports and wants to see every finance internship they can legally take in Europe. Trackr (the-trackr.com) has finance trackers for the UK, France, Italy and Germany, but none found for Switzerland or the rest of Europe, so they have been checking career pages by hand and still can't be sure they saw everything.

The tracker replaces that manual checking. It runs itself.

## Goals

- One website with one big table of internships.
- Covers Switzerland and the EU/EEA, including France, Italy and Germany, so everything is in one place.
- Sectors: banking, private equity, finance, fintech.
- Filter by country, sector, season and language. Language is a general filter: tick the languages you want on the website.
- Fully automatic. No regular manual work.
- Costs at most €5 a month to run.
- Built for one user, with nothing that blocks opening it to others later.

## Non-goals

- London and the rest of the UK. Mattia excluded London because Trackr covers it, and the UK needs a visa. A role listing both London and an eligible city stays in.
- South America. Mattia said Europe, so Peruvian work rights don't affect the table.
- Application tracking, accounts, logins and alerts.
- A daily "what changed" or by-country view. Mattia chose the table (layout A) over both.
- Roles that are never posted online, such as those filled through networking.

## Anti-goals

- Showing a role as closed when the real problem was a failed check.
- Going over the budget.
- Quietly implying complete coverage. The site must show when firms were last checked and which firms it couldn't check.

## Decided by Mattia

| Decision | Choice |
|---|---|
| Eligibility | Swiss passport, so Switzerland and EU/EEA |
| Roles | Banking, PE, finance, fintech |
| Countries | All of Switzerland and EU/EEA, including those Trackr covers |
| Medium | Website |
| Main view | One big table (layout A) |
| Language | Selectable filter |
| Upkeep | Fully automatic |
| Running cost | €5 a month maximum |
| Audience | Just Mattia at first, possibly others later |

## The table

Columns: firm, role, city, country, sector, season, languages required, closes, last checked, and a link to the original ad on every row.

Filters: country, sector, season, languages, and "open only" (on by default). Every column sorts.

Language filter behaviour: ticking languages hides roles that require any language not ticked. Roles with no stated language stay visible and show "Not stated".

Closed roles stay in the data so a firm's past dates remain visible. "Open only" hides them.

A short status line shows when the data was last updated, how many firms were checked, how many failed, and whether the AI budget ran out. A coverage note lists firms that could not be checked.

## How listings are collected

A scheduled job runs once a day. For each firm:

1. **Firms with a public feed** (Greenhouse, Lever, Ashby, Workday): read the feed. All four were tested live on 2026-11-04, and none needs a key. Workday's endpoint is undocumented, so it may change.
2. **All other firms:** fetch the careers page, using a real browser only when the plain page has almost no text. If the text hasn't changed since the last run, stop. If it has, AI reads it and lists the internships.

A multilingual title check picks likely internships. AI then reads each new role once to fill in country, season, closing date and required languages. Each role is only ever read once, which keeps cost low.

A role closes when its closing date passes or when it disappears from a firm that was checked successfully. A failed check never closes anything. Three zero-result runs in a row are needed before a firm that had open roles is treated as really having none, in case a site redesign breaks reading.

## Firm list

This sets the limit on coverage: a firm not on the list never gets checked.

- **At build time:** a researched starter list in a file, with each firm's name, sector, country and careers link or feed. Aim: at least 150 firms, weighted toward Switzerland.
- **Monthly:** a job reads FINMA's published list of authorised banks and securities firms for new Swiss names, then checks for free whether each has a Greenhouse, Lever or Ashby board. Matches are added. The rest go on an "unresolved" list.
- **Known gap:** no bulk download of the EU register was confirmed (ESMA), so outside Switzerland new firms won't be found automatically in the first version. This falls short of "everything" and should be revisited after launch.

## Budget

- **AI model:** Claude Haiku 5.5 (`claude-haiku-5-5`), $0.10 per million input tokens and $0.50 per million output, as of the 2026-10-06 price list.
- **Why not a stronger model:** a typical page read is about 6,000 tokens in and 500 out, or roughly $0.00085 on Haiku, so about 5,900 reads for $5. The same read on Opus 5.5 costs about $0.034, or about 147 reads, which can't cover this list. These are estimates to replace with real usage.
- **Cap:** $5 a month in code, which should stay under €5 at current rates, stopping at $4.90 so that a final call can't overrun. Pages and new roles left unread wait until the next month.
- **Hard backstop:** the API account uses prepaid credit with auto-reload off.
- **Hosting and scheduling:** GitHub Pages and GitHub Actions on a public repository, both free.

## Calls made by Claude (Mattia can overrule)

- Daily update.
- Python for the job, a plain static site with no framework.
- Public GitHub repository. The site has a public but unadvertised address, with search engines asked not to index it. This is the cheapest option, and it means the job data is technically visible to anyone. A private setup is possible but needs more work.
- Haiku rather than the default stronger model, because of the budget.
- Roles are kept when language is unstated, rather than hidden.
- Dropped Trackr's firm names as a source for the firm list, which was suggested earlier. Its terms of use couldn't be read, and FINMA and manual research avoid the question.

## Rules for collecting

Respect robots.txt, wait at least 1 second between requests to the same site, and send a generic user agent with no personal details. Page content is untrusted data: AI output is schema-checked, it gets no tools, and the website treats all text as plain text and only links to http(s) addresses.

Before opening the site to other people, re-check each source's terms. Collecting for yourself and publishing to others are different things.

## Left to the builder

Internal structure, page styling, retry details, and browser choice for pages that need one.
