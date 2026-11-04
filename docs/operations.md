# Operations

## Run locally

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m tracker run                      # a full run; needs network; uses GEMINI_API_KEY if set
```

```bash
python -m tracker run --steps sync,export  # offline: seed files in, site data out
```

```bash
python -m http.server -d site 8000         # then open http://localhost:8000
```

Other commands: `status`, `backup`, `restore`, `migrate`, `import-lead URL [--employer ID]`, `research ID`
(`python -m tracker --help`).

## On GitHub

The **Tracker run** workflow (`.github/workflows/run.yml`) runs six times a day and by hand. By-hand inputs: steps to
run, build the digest now, import a lead URL, research one firm. It commits `data/db` and `site/data` and deploys the
site. A failing run still commits its data and deploys, then marks the workflow failed. **Tests** run on every push.

## Monitoring

- **Sources** view: every source with health (working, degraded, failing, blocked, gone, disabled), last result,
  last error, checks in the last 7 days, yield and next check; AI usage and spend; search status; the work queue
  (waiting and given-up jobs); discovery candidates and their outcomes; coverage metrics and open gaps.
- The sidebar shows how many sources work, AI limits reached, today's digest and firms that must be checked by hand.
- Each run's statistics, step errors and notes are in the `runs` table (`python -m tracker status`) and the workflow log.
- Silent breakage is caught by health: a source that keeps failing turns *failing* after three checks, and a
  failure at a priority firm creates a notification.

## Reviewing discovery failures

Sources view → *Discovery — latest candidates* shows each candidate's outcome and why (e.g. "website does not name
the firm: identity not verified"). Unresolved candidates are retried monthly, four times at most. To help one along,
add it to `firms/candidates.yaml` with its official website, or add the firm to `firms/firms.yaml` directly.

## AI usage and spending

Every call is a row in `ai_calls` (task, prompt version, model, tokens, searches, estimated cost, outcome). The
Sources view summarises the month by task. Caching means unchanged content is never sent twice; page sources are
only read by the model when their text hash changes.

## Backup and restore

- Every run's commit is a full backup: `git log -- data/db` lists them, and `git checkout <commit> -- data/db`
  restores one.
- Before any migration, the dump is copied to `backups/<time>-pre-migration-vNNNN/` (local runs; in Actions use the
  git history).
- `python -m tracker backup` writes a standalone SQLite file to `backups/` that any SQLite tool can open.
- `python -m tracker restore <file or snapshot dir>` validates the backup (integrity and foreign keys), keeps the
  current state as `backups/<time>-pre-restore/`, then replaces `data/db`. Commit the result.

Tested: `tests/test_db.py` restores from a backup after deleting `data/db`, and loads an older dump through a newer
migration with a snapshot taken first.

## Rollback

Code and data are versioned together. To roll back a release: `git revert` the code commit; if a migration ran,
also restore `data/db` from the commit before it (the dump records its schema version, and older code refuses
tables it does not know rather than corrupting them).

## Your personal data

It is only in your browser. Use **Export** in the sidebar regularly; **Import** merges a file back (files from v1
still import). Clearing site data in the browser deletes it.
