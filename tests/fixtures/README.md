# Test fixtures

- `greenhouse.json`, `lever.json`, `ashby.json`, `workday_list.json`, `workday_job.json`, `finma_sample.pdf`: saved
  from the live sources on 2026-11-04 (v1), trimmed.
- `smartrecruiters_*.json`, `recruitee.json`, `personio.xml`, `careers_jsonld.html`: hand-built from each platform's
  public API documentation and schema.org's JobPosting type, because this development environment could not reach
  the live endpoints. The connectors for these platforms are therefore tested against documented shapes only, not
  against a live board; see docs/limitations.md.
