# Coverage measurement

The question is not "how many postings did we collect" but "what share of relevant postings did we find, how fast,
and why did we miss the rest". That needs ground truth the tracker did not produce.

## The benchmark (`benchmark/benchmark.yaml`)

Maintained by a person:

- `employers`: firms the tracker should cover (large banks, boutiques, Swiss and Peruvian institutions, PE/VC,
  different ATS platforms). Used for **source coverage**.
- `opportunities`: internships verified by hand on the employer's own site, with `verified_on` and, when the page
  states it, `published_on`. Used for **recall** and **detection latency**.
- `labels`: `{opportunity_id, relevant: true|false}` for surfaced postings. Used for **precision**.

The committed file starts with 15 employers and no verified opportunities or labels, so recall and precision show
"no data" until you add some. The target is about 100 employers and a few verified postings each, refreshed monthly.

## The audit

Runs weekly and whenever the benchmark file changes (`tracker/coverage.py`). For each verified opportunity it looks
for the record by URL, then by employer and title, and classifies every miss from the database state:
`unknown_employer`, `missing_source`, `broken_connector`, `extraction_failure`, `deduplication_error`,
`incorrect_classification`, `restricted_source`. Misses become coverage gaps (Sources view) and, where automatic,
actions: unknown employers become discovery candidates, missing sources trigger source discovery.

Search results feed the same mechanism without a benchmark: a posting found by search at an employer whose
monitored sources did not list it becomes a `missed_by_monitoring` gap and queues source discovery.

## Metrics (all computed, none estimated)

| Metric | Definition |
| --- | --- |
| discovery recall | benchmark opportunities found / benchmark opportunities |
| discovery precision | labelled relevant / labelled |
| detection latency | median days from `published_on` to first seen |
| source coverage | benchmark employers with an enabled, working source / benchmark employers |
| source reliability | successful checks / checks, last 30 days |
| possible duplicate rate | opportunities flagged as possible duplicates / opportunities |
| cost per new open opportunity | AI spend (30 days) / opportunities first seen and open in those 30 days |

Eligibility accuracy and recruitment outcomes need your private data, so they are not computed by the tracker.

## Avoiding search bias

The search planner reserves a quarter of each day's searches for the least-explored facets (country × sector ×
format × language), so discovery does not only reinforce the employers it already knows.
