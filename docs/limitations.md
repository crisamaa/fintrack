# Known limitations

## Not verified against live sources

The build environment of this overhaul could not reach the web (outbound connections were refused), so:

- **SmartRecruiters, Recruitee and Personio** connectors and the **JSON-LD** careers-page path are tested only against
  fixtures built from documentation. Greenhouse, Lever, Ashby, Workday and plain careers pages were verified live in
  v1 (2026-11-04) and their parsing is unchanged.
- **FINMA directory** parsing is tested against a saved PDF from v1; the live page layout may have changed.
- **Gemini** calls are tested against a mock transport and scripted replies; the request format is the v1 one, which
  worked live. Grounded search (`google_search` tool) has not been exercised against the live API.
- **Peruvian firms** in `firms/candidates.yaml` are names and official websites as known; discovery will verify
  each one on the first networked run, and reject any whose site does not name it.

The first scheduled run will show the real state of every source in the Sources view.

## Coverage

- Only what firms publish on boards and pages the tracker can read is seen. Internships filled through campus
  channels, referrals or LinkedIn are not, except as leads you import.
- Firms that refuse automated visits (`firms/blocked.yaml`, e.g. UBS) are not read; they are shown as "check by
  hand" and appear as outreach prospects.
- Employer discovery outside Switzerland depends on postings found by search or aggregators and on the candidate
  file: no EU, UK or Peruvian registry is integrated yet (ESMA, the FCA register and SMV/SBS were not available as
  machine-readable lists in a form verified for this build). Adding one is a function in `discovery.DIRECTORIES`.
- Web search is off by default and bounded to 20 queries a day when on.
- The coverage benchmark starts with employers only; recall and precision read "no data" until verified postings and
  labels are added by hand.

## Other

- Workday's endpoint is not formally documented and may change without notice (failures show as format changed).
- Careers pages that need JavaScript use a headless browser and may still come up empty.
- The AI can misread a page; every AI value is labelled as such, must be quoted from the source, and never overrides
  structured data, but it can still be wrong. Open the original ad before applying.
- Program windows for off-cycle and insight programs need an intake year in the title or text; without one they are
  linked to the program but no window is derived.
- Personal data lives in one browser; there is no sync across devices other than export/import.
- Collection is sequential (one source at a time, politely). With several hundred page sources a run may stop at the
  time limit and continue in the next run; nothing is lost.
- The site's data is public (public repository); the site asks search engines not to index it but is not private.

## Would add the most value next

1. A networked run to validate the new connectors and the Peruvian candidates, then fill the benchmark with ~100
   firms and verified postings.
2. Machine-readable registries for the EU (ESMA lists), the UK (FCA register API, needs a key) and Peru (SMV/SBS).
3. An email-forwarding import (e.g. a dedicated mailbox read with consent) for university newsletters.
4. Optional private hosting (a private repository plus a small authenticated backend) if the data should not be public.
