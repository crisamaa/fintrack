# Personalised Discover

Discover opens on **For you**: the feed of your active *career target*, reviewed in a list beside the detail pane.
**All opportunities** is the previous table, unchanged, with every filter. Coverage is the tracker's job; the
personalisation only decides what deserves your attention first. Nothing is ever deleted or hidden from All
opportunities.

## Pieces and where they live

| Piece | Where | Persistence |
| --- | --- | --- |
| Role classification | `tracker/roles.py`, run step `classify`, enrichment | `opportunity_roles` (migration 0002), exported as `role` |
| Career targets, decisions, events, suggestions, role corrections | `site/targets.js` | browser `localStorage` key `fintrack.targets.v1` |
| Matching engine | `site/match.js` | none: computed on load |
| Feed sections and resurfacing | `site/feed.js` | none |
| Learning (suggestions, behaviour nudge) | `site/learn.js` | suggestions in `fintrack.targets.v1` |
| Applications | `site/store.js` (unchanged) | `fintrack.tracker.v1`, one record per posting |

Personal data never leaves the browser (the repository is public). Export/Import in the sidebar now includes targets
(file version 3; older files still import).

## Role classification

Each posting gets a *function* — what the intern will do — independent of the employer's sector, so a bank's software
internship is `technology` and a private-equity firm's investor-relations internship is `investor_relations`. The
taxonomy (39 functions in 8 families; finance ids reuse `taxonomy.SECTORS`) is in `tracker/roles.py`, with a copy in
`site/match.js` that `tests/test_roles.py` keeps identical.

Layers, each used only when the previous found nothing specific:

1. Job title rules — `high` confidence for a specific function, `medium` for a generic one ("Investment Intern").
   What a person does outranks the area they do it in: "Software Engineer – Global Markets" is technology, with sales &
   trading kept as a secondary function.
2. The department the job board states — `medium`.
3. The same finance term repeated in the description (twice or more, clearly ahead of any other) — `low`.
4. Gemini, inside the existing enrichment call (prompt `enrich/4`), only when the rules ended at `low`/none or a
   generic function, and only with a verbatim quote from the posting — `medium`, method `ai`.
5. Otherwise `unknown`. The employer's sector is never used to guess.

A posting is reclassified only when its title, department or description change (content hash) or the classifier
`VERSION` changes; the `classify` step backfills before every export. A change of function is written to the
posting's history (`opportunity_events`, field `role`). Your corrections (the "Correct…" control in the detail pane)
are stored in the browser and apply to every target; the tracker's classification is never rewritten.

## Matching (`site/match.js`, `MATCH_VERSION = match-1`)

Eligibility (can you apply?) comes from `eligibility.js` and is shown beside the match; it never changes the score
or category. Relevance (does it fit this target?) is:

1. **Hard constraints** first: an excluded function or employer; a country, format or intake year the target marks
   *required*. A posting that clearly fails one is `excluded` from that target's feed. Missing data never fails a
   requirement; the posting goes to Needs review instead.
2. **Weighted score** over the dimensions the target actually sets (role and "other" always count):

   | Factor | Weight | Scoring (0–1) |
   | --- | ---: | --- |
   | Role function | 40 | Strong 1 · OK 0.75 · same family as a chosen function 0.55 (generic "investing" 0.6) · other finance 0.3 · non-finance 0.05 · Lower 0.2 · unknown 0.4 (0.55 if the firm works in a chosen function, stated as such). A secondary function counts at 0.8× under a finance primary, at most 0.35 under a non-finance primary. Low confidence is pulled toward 0.4. |
   | Places | 20 | preferred city 1 · preferred country 1 (0.85 when cities are set) · elsewhere 0.2 · deprioritised 0.15 · unknown 0.5 |
   | Timing & format | 15 | format 60 %, intake 40 %: chosen 1 · other 0.25/0.1 · deprioritised 0.1 · unknown 0.5 |
   | Employer | 10 | preferred 1 · preferred kind of employer 0.75 · other 0.3 |
   | Other | 10 | 0.7, or 0.35 if not yet confirmed on the employer's site; ±0.3 for working languages |
   | Behaviour | 5 | 0.5 + 0.5 × the per-function nudge from `learn.js`; never affects the category |

   Score = 100 × Σ(weight × value) / Σ(weights of active dimensions). Averaging only over what the target sets means
   a target silent about places does not dilute everything, and postings with more fields are not rewarded for that.
3. **Category**: `excluded` (hard constraint) · `review` (role or a required dimension uncertain, role value ≥ 0.5) ·
   `top` (role ≥ 0.9, score ≥ 75, no place/timing factor against it) · `good` (role ≥ 0.7, score ≥ 55) · `adjacent`
   (role ≥ 0.45) · `low`. Every factor adds one reason line (+ in favour, − against, ? uncertain, · note). The score
   orders the list and is never shown; it is not a probability.

## Feed sections (`site/feed.js`)

One evaluation, several queries; decisions show the same everywhere and no section lists a posting twice.

- **Top matches** — top and good, not applied, not ineligible. Ordered by score, +3 eligible / +2 sponsorship / −3
  language gap, +2 if it closes within 14 days.
- **Newly found** — top/good/adjacent/review postings *first found by the tracker* since your previous visit to this
  target or in the last 7 days, whichever is longer. Re-verification never makes a posting new, and a new target does
  not mark the history as new.
- **Closing soon** — stated closing date 0–21 days away. Rolling or missing dates are never "closing". Recurring
  programs opening within 45 days are listed apart, estimates marked as estimates.
- **Needs review** — `review` matches, and dismissed postings that changed materially.
- **Adjacent**, **Saved & applied**, **Dismissed** — secondary tabs. Applied postings (Pipeline stage Applied or
  later) leave the queue in every target and stay listed under Saved & applied.

**Resurfacing.** A dismissal stores a fingerprint (function, place, format, intake, reopen count, eligibility,
category, preferences version). It returns to Needs review — labelled, with the change explained and the dismissal
kept — only if, for *this* target, the posting is relevant now and: its function changed; it moved to a preferred
place; its intake changed; it reopened; eligibility became favourable; or your preferences changed and it went from
adjacent/low to good/top. Rechecks, rewording, new sources and moved dates do not count.

## Decisions and learning

Save (also shortlists in Pipeline once, remembering the originating target), Dismiss (optional reason chips in the
undo bar; Undo or `u`), Mark applied (Pipeline, all targets), Later (kept, listed last). Each is an event in the
target's history; undo marks the event reversed and keeps both.

`learn.js` proposes a change only from explicit, independent evidence in the active target: 3+ postings across 2+
firms dismissed with a role/location/timing reason (2 postings for an employer), or 3+ saves/applications of an
unselected function across 2+ firms. Undone decisions and reasonless dismissals are not evidence. A suggestion
names the target and its effect, and changes nothing until you press *Apply suggestion*, which saves an ordinary
revision (Edit target → Changes to this target → undo). *Not now* hides it for 3 days; *Dismiss suggestion* keeps
it away until there are 2 more pieces of evidence and 14 days have passed. Learning needs no AI and no network.

## Migration of existing browser data

Nothing is rewritten. Existing applications stay in Pipeline and are shown in every target. The old global "not
interested" list counts as dismissed in every target (labelled "not interested") and can be restored per target. The
Profile's sectors/countries/formats prefill the first target's setup, with a note saying so; they still drive the Fit
column of All opportunities. Work authorisation, degree and languages stay global in Profile.

## Operating it

- The next scheduled tracker run applies migration 0002, classifies every posting and exports `role`. Until then
  the deployed data has no `role`, so For you shows every role as "not determined" (Needs review / Low).
- Bump `roles.VERSION` after changing rules: the next run reclassifies everything (rules only, no AI cost).
- Bump `MATCH_VERSION` and `match.test.mjs` expectations after changing weights or thresholds.
- Try the UI with fresh classifications without touching `data/db`: see `tests/e2e/discover_scenarios.py` for the
  local site build and the scripted browser walk-through (49 checks).
