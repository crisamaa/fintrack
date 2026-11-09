"""One scheduled run: every step isolated, the database saved whatever happens.

Steps, in order (each can be selected with --steps):
  sync      seed files -> database (and the one-time v1 import)
  collect   check the sources that are due (deterministic; never needs AI)
  maintain  expire past deadlines; mark long-unconfirmed opportunities unavailable (never closed)
  work      drain the job queue: page extraction, enrichment, verification, source discovery, research
  discover  financial directories (monthly) and a bounded batch of employer candidates
  search    market-wide web search, if a provider is configured
  programs  program window estimates
  prospects score prospects and queue research
  audit     coverage benchmark (weekly, or when the benchmark file changed)
  notify    program-opening notifications; the digest at the configured hour
  classify  role function of opportunities not yet classified at the current classifier version (rules only)
  export    site data files
A step that raises is logged and recorded in the run's stats; the following steps still run.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tracker import (coverage, db, discovery, export, ingest, jobs, legacy, notify, programs, prospects, roles,
                     search, seeds, work)
from tracker.ai import AIService, GeminiProvider, Provider
from tracker.budget import Budget
from tracker.config import Config
from tracker.context import Ctx
from tracker.db import dumps, get_meta, iso, parse_iso, set_meta
from tracker.http import SafeClient

log = logging.getLogger(__name__)
STEPS = ("sync", "collect", "maintain", "work", "discover", "search", "programs", "prospects", "audit", "notify",
         "classify", "export")
LOCK_MINUTES = 70
AUDIT_EVERY = timedelta(days=7)


class RunLocked(Exception):
    pass


class RunFailed(Exception):
    """The run saved its data but should exit non-zero."""


@dataclass
class Report:
    run_id: int
    status: str
    stats: dict
    errors: list[str] = field(default_factory=list)


def prune(conn, now: datetime) -> None:
    conn.execute("DELETE FROM source_checks WHERE at < ?", (iso(now - timedelta(days=45)),))
    conn.execute("DELETE FROM ai_cache WHERE created_at < ?", (iso(now - timedelta(days=120)),))
    conn.execute("DELETE FROM notifications WHERE digested_at IS NOT NULL AND digested_at < ?",
                 (iso(now - timedelta(days=60)),))
    conn.execute("DELETE FROM audit_events WHERE at < ?", (iso(now - timedelta(days=365)),))
    conn.execute("DELETE FROM jobs WHERE status = 'done' AND updated_at < ?", (iso(now - timedelta(days=30)),))
    conn.execute("UPDATE source_postings SET description = '' WHERE present = 0 AND last_seen_at < ?",
                 (iso(now - timedelta(days=180)),))


def run(cfg: Config, *, now: datetime | None = None, steps: tuple[str, ...] = STEPS, http: SafeClient | None = None,
        provider: Provider | None = None, force_digest: bool = False, clock=time.monotonic) -> Report:
    now = now or datetime.now(UTC).replace(microsecond=0)
    conn = db.load(cfg.db_dir, now, backup_dir=cfg.backup_dir)
    lock = parse_iso(get_meta(conn, "run_lock"))
    if lock and lock > now:
        raise RunLocked(f"another run holds the lock until {iso(lock)}")
    set_meta(conn, "run_lock", iso(now + timedelta(minutes=LOCK_MINUTES)))
    budget = Budget(conn, now, monthly_cap_usd=cfg.ai_monthly_cap_usd, daily_requests=cfg.ai_daily_requests,
                    daily_searches=cfg.search_daily_limit)
    if provider is None and cfg.gemini_api_key:
        provider = GeminiProvider(cfg.gemini_api_key)
    ai = AIService(conn, provider, budget, now)
    owns_http = http is None
    http = http or SafeClient()
    run_id = conn.execute("INSERT INTO runs (kind, started_at, status) VALUES ('run', ?, 'running')",
                          (iso(now),)).lastrowid
    ctx = Ctx(conn=conn, now=now, config=cfg, http=http, ai=ai, run_id=run_id, clock=clock, started=clock(),
              deadline_s=cfg.run_minutes * 60)
    jobs.recover(conn, now)
    step_errors: dict[str, str] = {}
    digest_data = None
    for step in steps:
        try:
            log.info("step %s", step)
            if step == "sync":
                ctx.stats.update({f"seed_{k}": v for k, v in seeds.sync(ctx, cfg.root / "firms").items()})
                report = legacy.import_v1(ctx, cfg.data_dir / "legacy")
                if report:
                    ctx.stats["legacy_imported"] = report["imported"]
            elif step == "collect":
                ctx.stats["sources_checked"] = work.collect(ctx)
            elif step == "maintain":
                ctx.stats.update(ingest.maintain(ctx))
            elif step == "work":
                work.drain(ctx)
            elif step == "discover":
                ctx.stats.update({f"directory_{k}": v for k, v in discovery.run_directories(ctx).items()
                                  if isinstance(v, int)})
                discovery.process_candidates(ctx)
                work.drain(ctx, ["discover_sources"])
            elif step == "search":
                search.run_search(ctx)
                work.drain(ctx, ["verify"])
            elif step == "programs":
                ctx.stats["program_estimates"] = programs.estimate_all(ctx)
            elif step == "prospects":
                ctx.stats["prospects"] = prospects.score_all(ctx)
                prospects.research_due(ctx)
                work.drain(ctx, ["research"])
            elif step == "audit":
                path = cfg.root / "benchmark" / "benchmark.yaml"
                digest_of = hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else "none"
                last = parse_iso(get_meta(conn, "audit:last"))
                if digest_of != get_meta(conn, "audit:benchmark") or not last or now - last >= AUDIT_EVERY:
                    result = coverage.audit(ctx, path)
                    set_meta(conn, "audit:last", iso(now))
                    set_meta(conn, "audit:benchmark", digest_of)
                    ctx.stats["audit_misses"] = len(result["misses"])
            elif step == "classify":
                ctx.stats["roles_classified"] = roles.refresh(conn, now)
            elif step == "notify":
                notify.program_openings(ctx)
                last = parse_iso(get_meta(conn, "digest:last"))
                due = now.hour == cfg.digest_hour_utc and (not last or now - last > timedelta(hours=20))
                if force_digest or due:
                    digest_data = notify.digest(ctx)
                    set_meta(conn, "digest:last", iso(now))
                    notify.post_github_issue(ctx, digest_data)
            elif step == "export":
                prune(conn, now)
                ai.flush()
                ctx.stats["export_bytes"] = sum(export.write_all(ctx, budget, digest_data).values())
        except Exception as e:  # isolate: record and continue
            log.exception("step %s failed", step)
            step_errors[step] = f"{type(e).__name__}: {e}"
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        finally:
            ai.flush()
    checks_failed = sum(v for k, v in ctx.stats.items() if k.startswith("check_"))
    all_failed = ctx.stats.get("sources_checked", 0) > 0 and ctx.stats.get("checks_ok", 0) == 0 \
        and checks_failed == ctx.stats.get("sources_checked", 0)
    status = "failed" if all_failed or ai.blocked_reason == "account_error" else \
        ("partial" if step_errors or ctx.errors else "ok")
    stats = dict(ctx.stats) | {"step_errors": step_errors, "ai_blocked": ai.blocked_reason,
                               "budget_exhausted": budget.exhausted_reason,
                               "http_requests": http.stats.requests}
    conn.execute("UPDATE runs SET finished_at = ?, status = ?, stats = ?, note = ? WHERE id = ?",
                 (iso(datetime.now(UTC) if now is None else now), status, dumps(stats),
                  "; ".join(ctx.errors[:10]) or None, run_id))
    set_meta(conn, "run_lock", iso(now - timedelta(seconds=1)))
    db.save(conn, cfg.db_dir)
    if owns_http:
        http.close()
    return Report(run_id, status, stats, ctx.errors + [f"{k}: {v}" for k, v in step_errors.items()])


def warn_uncapped(cfg: Config) -> str | None:
    if cfg.ai_monthly_cap_usd is not None:
        return None
    msg = ("No AI spending cap is set. Fine for a free-tier Gemini key. If this key is billed, set the repository "
           "variable AI_MONTHLY_CAP_USD (e.g. 5). The Sources page shows what this month would cost at paid prices.")
    log.warning(msg)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=No AI spending cap::{msg}", flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(f"### ⚠️ No AI spending cap\n\n{msg}\n")
    return msg


def default_root() -> Path:
    return Path(".")
