"""Write the static site's data files from the database. The site never sees secrets or anything personal.

  site/data/opportunities.json  vacancies with requirements, sources, provenance and recent events
  site/data/market.json         employers, programs with windows, prospects with research notes
  site/data/ops.json            sources and their health, runs, jobs, discovery, AI usage, coverage
  site/data/digest.json         the latest daily digest
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from tracker import coverage, jobs, roles
from tracker.budget import Budget
from tracker.context import Ctx
from tracker.db import iso, loads, one


def _write(path: Path, data) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tmp.write_text(text)
    os.replace(tmp, path)
    return len(text)


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return out


def opportunities(ctx: Ctx) -> dict:
    c = ctx.conn
    reqs = _group(c.execute("SELECT * FROM opportunity_requirements ORDER BY kind").fetchall(), "opportunity_id")
    fcts = _group(c.execute("SELECT * FROM opportunity_facts").fetchall(), "opportunity_id")
    sps = _group(c.execute("SELECT sp.opportunity_id, sp.source_id, sp.url, sp.present, sp.first_seen_at, "
                           "sp.last_seen_at, sp.extraction, s.kind, s.association, s.category FROM source_postings sp "
                           "JOIN sources s ON s.id = sp.source_id WHERE sp.opportunity_id IS NOT NULL").fetchall(),
                 "opportunity_id")
    evs = _group(c.execute("SELECT * FROM opportunity_events ORDER BY at DESC, id DESC").fetchall(), "opportunity_id")
    role_rows = {r["opportunity_id"]: r for r in c.execute("SELECT * FROM opportunity_roles")}
    out = []
    for o in c.execute("SELECT o.*, e.name employer FROM opportunities o JOIN employers e ON e.id = o.employer_id "
                       "ORDER BY o.employer_id, o.title, o.id"):
        oid = o["id"]
        out.append({
            "id": oid, "employer_id": o["employer_id"], "employer": o["employer"], "title": o["title"],
            "url": o["canonical_url"], "status": o["status"], "internship_type": o["internship_type"],
            "intake_year": o["intake_year"], "city": o["city"], "countries": loads(o["countries"], []),
            "sectors": loads(o["sectors"], []), "languages": loads(o["languages"], []), "closes_on": o["closes_on"],
            "published_at": o["published_at"], "first_seen_at": o["first_seen_at"],
            "last_verified_at": o["last_verified_at"], "closed_at": o["closed_at"], "close_reason": o["close_reason"],
            "reopened": o["reopened_count"], "discovered_via": o["discovered_via"], "program_id": o["program_id"],
            "review_flag": o["review_flag"], "excluded_reason": o["excluded_reason"], "enrichment": o["enrichment"],
            "description": o["description"][:600],
            "role": roles.for_export(role_rows[oid]) if oid in role_rows else None,
            "requirements": [{"kind": r["kind"], "value": loads(r["value"]), "evidence": r["evidence"],
                              "method": r["method"]} for r in reqs.get(oid, [])],
            "sources": [{"source_id": s["source_id"], "url": s["url"], "present": bool(s["present"]),
                         "first_seen_at": s["first_seen_at"], "last_seen_at": s["last_seen_at"], "kind": s["kind"],
                         "association": s["association"], "category": s["category"],
                         "extraction": s["extraction"]} for s in sps.get(oid, [])],
            "facts": [{"field": f["field"], "method": f["method"], "value": loads(f["value"]),
                       "source_id": f["source_id"], "model": f["model"], "prompt_version": f["prompt_version"],
                       "evidence": f["evidence"]} for f in fcts.get(oid, [])],
            "events": [{"at": e["at"], "event": e["event"], "from": e["from_status"], "to": e["to_status"],
                        "field": e["field"], "old": e["old_value"], "new": e["new_value"], "note": e["note"]}
                       for e in evs.get(oid, [])[:12]],
        })
    return {"generated_at": iso(ctx.now), "role_taxonomy": roles.taxonomy(), "opportunities": out}


def market(ctx: Ctx) -> dict:
    c = ctx.conn
    sectors = _group(c.execute("SELECT * FROM employer_sectors").fetchall(), "employer_id")
    locs = _group(c.execute("SELECT * FROM employer_locations").fetchall(), "employer_id")
    srcs = _group(c.execute("SELECT id, employer_id FROM sources WHERE employer_id IS NOT NULL").fetchall(),
                  "employer_id")
    counts = {r["employer_id"]: r for r in c.execute(
        "SELECT employer_id, SUM(status = 'open' AND excluded_reason IS NULL) open, COUNT(*) total FROM opportunities "
        "GROUP BY employer_id")}
    aliases = _group(c.execute("SELECT * FROM employer_aliases").fetchall(), "employer_id")
    employers = [{
        "id": e["id"], "name": e["name"], "website": e["website"], "hq_country": e["hq_country"],
        "sectors": [s["sector"] for s in sectors.get(e["id"], [])],
        "countries": sorted({x["country"] for x in locs.get(e["id"], [])}),
        "priority": e["priority"], "access_note": e["access_note"], "discovered_via": e["discovered_via"],
        "discovery_evidence": e["discovery_evidence"], "size_hint": e["size_hint"], "created_at": e["created_at"],
        "aliases": sorted({a["alias"] for a in aliases.get(e["id"], [])} - {e["name"]}),
        "source_ids": [s["id"] for s in srcs.get(e["id"], [])],
        "open": (counts.get(e["id"]) or {"open": 0})["open"] or 0,
        "total": (counts.get(e["id"]) or {"total": 0})["total"] or 0,
    } for e in c.execute("SELECT * FROM employers WHERE status = 'active' ORDER BY name")]
    wins = _group(c.execute("SELECT * FROM program_windows ORDER BY intake_year DESC, kind").fetchall(), "program_id")
    popps = _group(c.execute("SELECT id, program_id FROM opportunities WHERE program_id IS NOT NULL").fetchall(),
                   "program_id")
    progs = [{
        "id": p["id"], "employer_id": p["employer_id"], "name": p["name"], "type": p["program_type"],
        "division": p["division"], "countries": loads(p["countries"], []), "official_url": p["official_url"],
        "eligibility": p["eligibility_text"], "status": p["status"], "discovered_via": p["discovered_via"],
        "opportunity_ids": [o["id"] for o in popps.get(p["id"], [])],
        "role": roles.classify(p["name"], description=p["eligibility_text"] or ""),
        "windows": [{k: w[k] for k in ("intake_year", "kind", "opens_on", "closes_on", "opens_earliest",
                                       "opens_latest", "source_url", "evidence", "recorded_at")}
                    for w in wins.get(p["id"], [])],
    } for p in c.execute("SELECT * FROM programs ORDER BY employer_id, name")]
    intel = _group(c.execute("SELECT * FROM employer_intel ORDER BY kind, id").fetchall(), "employer_id")
    prospects = [{
        "employer_id": p["employer_id"], "status": p["status"], "score": p["score"],
        "reasons": loads(p["reasons"], []), "summary": p["summary"], "application_channel": p["application_channel"],
        "researched_at": p["researched_at"], "model": p["model"],
        "intel": [{"kind": i["kind"], "text": i["text"], "evidence": i["evidence"], "source_url": i["source_url"],
                   "recorded_at": i["recorded_at"]} for i in intel.get(p["employer_id"], [])],
    } for p in c.execute("SELECT * FROM prospects ORDER BY score DESC, employer_id")]
    return {"generated_at": iso(ctx.now), "employers": employers, "programs": progs, "prospects": prospects}


def ops(ctx: Ctx, budget: Budget) -> dict:
    c = ctx.conn
    since = iso(ctx.now - timedelta(days=7))
    outcomes = _group(c.execute("SELECT source_id, outcome, COUNT(*) n FROM source_checks WHERE at >= ? GROUP BY "
                                "source_id, outcome", (since,)).fetchall(), "source_id")
    sources = []
    for s in c.execute("SELECT * FROM sources ORDER BY employer_id, id"):
        d = {k: s[k] for k in ("id", "employer_id", "kind", "url", "category", "association", "access", "health",
                               "enabled", "priority", "last_success_at", "last_attempt_at", "last_change_at",
                               "last_outcome", "last_error", "consecutive_failures", "checks_total", "checks_failed",
                               "postings_total", "next_check_at", "discovery_method", "discovery_evidence",
                               "created_at")}
        d["checks_7d"] = {r["outcome"]: r["n"] for r in outcomes.get(s["id"], [])}
        sources.append(d)
    runs = [dict(r) | {"stats": loads(r["stats"], {})} for r in
            c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 30")]
    month = iso(ctx.now)[:7]
    by_task = [dict(r) for r in c.execute(
        "SELECT task, outcome, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens, "
        "SUM(search_calls) searches, ROUND(SUM(cost_usd), 4) cost_usd FROM ai_calls WHERE substr(at, 1, 7) = ? "
        "GROUP BY task, outcome ORDER BY task, outcome", (month,))]
    cand = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM employer_candidates GROUP BY status")}
    recent_cands = [dict(r) for r in c.execute(
        "SELECT name, country, origin, status, note, employer_id, updated_at FROM employer_candidates ORDER BY "
        "updated_at DESC, id DESC LIMIT 60")]
    gaps = [dict(r) for r in c.execute("SELECT * FROM coverage_gaps WHERE resolved_at IS NULL ORDER BY detected_at "
                                       "DESC LIMIT 100")]
    dead = [dict(r) for r in c.execute("SELECT kind, key, attempts, last_error, updated_at FROM jobs WHERE status = "
                                       "'dead' ORDER BY updated_at DESC LIMIT 50")]
    queries = one(c, "SELECT COUNT(*) n, SUM(runs > 0) tried, SUM(new_opportunities) opps, SUM(new_employers) emps "
                  "FROM search_queries")
    return {
        "generated_at": iso(ctx.now), "sources": sources, "runs": runs, "jobs": jobs.summary(c), "dead_jobs": dead,
        "candidates": {"by_status": cand, "recent": recent_cands}, "coverage_gaps": gaps,
        "coverage": coverage.latest(c),
        "ai": {"model": ctx.ai.provider.model if ctx.ai and ctx.ai.provider else None,
               "available": bool(ctx.ai and ctx.ai.available),
               "blocked_reason": ctx.ai.blocked_reason if ctx.ai else "not configured",
               "month": month, "spent_usd": round(budget.spent_month(), 4), "cap_usd": budget.cap,
               "requests_today": budget.requests_today(), "daily_requests": budget.daily_requests,
               "searches_today": budget.searches_today(), "searches_month": budget.searches_month(),
               "daily_searches": budget.daily_searches, "exhausted": budget.exhausted_reason,
               "search_provider": ctx.config.search_provider, "by_task": by_task},
        "search": {"queries": queries["n"] or 0, "tried": queries["tried"] or 0,
                   "new_opportunities": queries["opps"] or 0, "new_employers": queries["emps"] or 0},
        "directories": {r["key"].split(":")[1]: r["value"] for r in
                        c.execute("SELECT * FROM meta WHERE key LIKE 'directory:%:last_success'")},
    }


def write_all(ctx: Ctx, budget: Budget, digest: dict | None = None) -> dict[str, int]:
    site = ctx.config.site_dir / "data"
    sizes = {"opportunities.json": _write(site / "opportunities.json", opportunities(ctx)),
             "market.json": _write(site / "market.json", market(ctx)),
             "ops.json": _write(site / "ops.json", ops(ctx, budget))}
    if digest is not None:
        sizes["digest.json"] = _write(site / "digest.json", digest)
    elif not (site / "digest.json").exists():
        sizes["digest.json"] = _write(site / "digest.json", {"generated_at": None, "items": []})
    return sizes
