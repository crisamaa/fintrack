"""Command line: python -m tracker <command>.

  run [--steps collect,work,...] [--digest]   one scheduled run (what GitHub Actions calls)
  status                                      summary of the database: sources, opportunities, jobs, AI spend
  backup [--out FILE]                         write a standalone SQLite copy (default backups/fintrack-<time>.sqlite)
  restore SOURCE                              restore data/db from a backup file or snapshot directory
  migrate                                     apply pending migrations and save (also happens on every load)
  import-lead URL [--employer ID]             add a posting or board URL you came across (newsletter, careers fair)
  research EMPLOYER_ID                        research one firm now (uses AI)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from tracker import db
from tracker.config import load_config


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--steps", default="")
    r.add_argument("--digest", action="store_true")
    sub.add_parser("status")
    b = sub.add_parser("backup")
    b.add_argument("--out")
    rs = sub.add_parser("restore")
    rs.add_argument("source")
    sub.add_parser("migrate")
    il = sub.add_parser("import-lead")
    il.add_argument("url")
    il.add_argument("--employer")
    rc = sub.add_parser("research")
    rc.add_argument("employer_id")
    args = ap.parse_args(argv)

    try:
        cfg = load_config(Path("."))
    except ValueError as e:
        logging.error("%s", e)
        return 1

    if args.cmd == "run":
        from tracker.run import STEPS, RunLocked, run, warn_uncapped
        steps = tuple(s for s in args.steps.split(",") if s) or STEPS
        unknown = set(steps) - set(STEPS)
        if unknown:
            logging.error("unknown steps: %s", ", ".join(sorted(unknown)))
            return 1
        try:
            report = run(cfg, steps=steps, force_digest=args.digest)
        except RunLocked as e:
            logging.error("%s", e)
            return 1
        finally:
            warn_uncapped(cfg)
        print(json.dumps({"run": report.run_id, "status": report.status, "stats": report.stats}, indent=1,
                         default=str))
        for err in report.errors[:20]:
            logging.warning("%s", err)
        return 1 if report.status == "failed" else 0

    now = _now()
    if args.cmd == "status":
        conn = db.load(cfg.db_dir, now)
        q = lambda sql: [dict(r) for r in conn.execute(sql)]  # noqa: E731
        print(json.dumps({
            "schema_version": db.schema_version(conn),
            "counts": {k: v for k, v in db.counts(conn).items() if v},
            "sources_by_health": q("SELECT health, COUNT(*) n FROM sources GROUP BY health"),
            "opportunities_by_status": q("SELECT status, COUNT(*) n FROM opportunities GROUP BY status"),
            "jobs": q("SELECT kind, status, COUNT(*) n FROM jobs GROUP BY kind, status"),
            "ai_month": q("SELECT substr(at,1,7) month, COUNT(*) calls, ROUND(SUM(cost_usd),4) usd FROM ai_calls "
                          "GROUP BY month ORDER BY month DESC LIMIT 3"),
            "last_runs": q("SELECT id, started_at, status FROM runs ORDER BY id DESC LIMIT 5"),
        }, indent=1))
        return 0
    if args.cmd == "backup":
        conn = db.load(cfg.db_dir, now)
        out = Path(args.out or cfg.backup_dir / f"fintrack-{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite")
        print(db.backup_sqlite(conn, out))
        return 0
    if args.cmd == "restore":
        if cfg.db_dir.exists():
            db.snapshot(cfg.db_dir, cfg.backup_dir, "pre-restore", now)  # the current state is kept first
        print(json.dumps(db.restore(Path(args.source), cfg.db_dir, now), indent=1))
        return 0
    if args.cmd == "migrate":
        conn = db.load(cfg.db_dir, now, backup_dir=cfg.backup_dir)
        db.save(conn, cfg.db_dir)
        print(f"schema version {db.schema_version(conn)}")
        return 0
    if args.cmd == "import-lead":
        from tracker.leads import import_lead
        print(json.dumps(import_lead(cfg, args.url, employer_id=args.employer, now=now), indent=1))
        return 0
    if args.cmd == "research":
        from tracker import jobs
        from tracker.run import run
        conn = db.load(cfg.db_dir, now)
        jobs.enqueue(conn, now, "research", args.employer_id, {"requested": True}, refresh=True)
        db.save(conn, cfg.db_dir)
        report = run(cfg, steps=("work", "export"))
        print(json.dumps({"status": report.status, "stats": report.stats}, indent=1, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
