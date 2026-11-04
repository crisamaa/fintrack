"""Coverage measurement (Workflow J) against an independently verified benchmark: benchmark/benchmark.yaml.

The benchmark is written by a person, not by this system: employers that should be covered, opportunities someone
verified by hand on the employer's site (with the date), and relevance labels for surfaced opportunities. Every
metric is computed from those records and the database; a metric without data is reported as null with the
reason, never estimated.

For each verified opportunity the system did not find, the cause is classified from the database state, recorded as
a coverage gap, and turned into an action where one is automatic (queue source discovery, add a candidate).
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from pathlib import Path

import yaml

from tracker import entities, jobs
from tracker.context import Ctx
from tracker.db import dumps, iso, loads, one, tx
from tracker.identity import canonical_url, title_key


def load_benchmark(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {"version": None, "employers": [], "opportunities": [], "labels": []}
    data = yaml.safe_load(path.read_text()) or {}
    for key in ("employers", "opportunities", "labels"):
        data[key] = data.get(key) or []
    return data


def _find(conn, employer_id: str | None, item: dict):
    if item.get("url"):
        canon = canonical_url(item["url"])
        row = one(conn, "SELECT * FROM opportunities WHERE canonical_url = ?", (canon,))
        if row:
            return row
        row = one(conn, "SELECT o.* FROM source_postings sp JOIN opportunities o ON o.id = sp.opportunity_id WHERE "
                  "sp.url = ?", (item["url"],))
        if row:
            return row
    if employer_id and item.get("title"):
        return one(conn, "SELECT * FROM opportunities WHERE employer_id = ? AND title_key = ?",
                   (employer_id, title_key(item["title"])))
    return None


def _cause(conn, employer_id: str | None, item: dict, found) -> tuple[str, str]:
    if found is not None:
        if found["excluded_reason"] == "not_internship":
            return "incorrect_classification", "found but classified as not an internship"
        if found["excluded_reason"] == "outside_countries":
            return "incorrect_classification", "found but excluded as outside the countries of interest"
        return "", ""
    if employer_id is None:
        return "unknown_employer", f"{item.get('employer')!r} is not a tracked employer"
    e = one(conn, "SELECT access_note FROM employers WHERE id = ?", (employer_id,))
    if e["access_note"]:
        return "restricted_source", e["access_note"]
    sources = conn.execute("SELECT * FROM sources WHERE employer_id = ? AND enabled = 1 AND category NOT IN "
                           "('aggregator', 'import')", (employer_id,)).fetchall()
    if not sources:
        return "missing_source", "the employer has no monitored recruitment source"
    if all(s["health"] in ("failing", "blocked", "gone") for s in sources):
        return "broken_connector", ", ".join(f"{s['id']}={s['health']}" for s in sources)
    if item.get("title"):
        key = title_key(item["title"])
        for s in sources:
            for p in conn.execute("SELECT title, opportunity_id FROM source_postings WHERE source_id = ?", (s["id"],)):
                if title_key(p["title"]) == key:
                    return "deduplication_error", f"posting seen on {s['id']} but merged or unlinked"
    if any(s["kind"] == "page" for s in sources):
        return "extraction_failure", "a careers page is monitored but the posting was not extracted"
    return "missing_source", "the posting is on a channel we do not monitor (e.g. another ATS or region)"


def audit(ctx: Ctx, path: Path) -> dict:
    bench = load_benchmark(path)
    conn = ctx.conn
    misses, hits, latencies = [], [], []
    with tx(conn):
        for item in bench["opportunities"]:
            eid = entities.resolve(conn, item.get("employer"), website=item.get("employer_website"))
            found = _find(conn, eid, item)
            cause, detail = _cause(conn, eid, item, found)
            if not cause:
                hits.append(found["id"])
                if item.get("published_on"):
                    lag = (date.fromisoformat(found["first_seen_at"][:10]) -
                           date.fromisoformat(str(item["published_on"]))).days
                    latencies.append(max(0, lag))
                continue
            misses.append({"employer": item.get("employer"), "title": item.get("title"), "url": item.get("url"),
                           "cause": cause, "detail": detail, "action": _act(ctx, eid, item, cause)})
            conn.execute("INSERT OR IGNORE INTO coverage_gaps (employer_id, opportunity_id, cause, detail, "
                         "detected_by, detected_at) VALUES (?, ?, ?, ?, 'benchmark', ?)",
                         (eid, found["id"] if found else None, cause, f"{item.get('title')}: {detail}"[:500],
                          iso(ctx.now)))
        metrics = _metrics(ctx, bench, hits, latencies)
        conn.execute("INSERT INTO coverage_evaluations (at, benchmark_version, metrics, misses) VALUES (?, ?, ?, ?)",
                     (iso(ctx.now), str(bench.get("version")), dumps(metrics), dumps(misses)))
    return {"metrics": metrics, "misses": misses}


def _act(ctx: Ctx, eid: str | None, item: dict, cause: str) -> str:
    if cause == "unknown_employer" and item.get("employer"):
        entities.add_candidate(ctx.conn, ctx.now, name=item["employer"], origin="benchmark",
                               website=item.get("employer_website"), evidence_url=item.get("url"),
                               evidence="missed benchmark opportunity")
        return "added as a discovery candidate"
    if cause in ("missing_source", "extraction_failure") and eid:
        jobs.enqueue(ctx.conn, ctx.now, "discover_sources", eid, {"reason": f"benchmark:{cause}"}, refresh=True)
        return "queued source discovery for this employer"
    if cause == "broken_connector":
        return "check the failing source(s) in Sources"
    if cause == "restricted_source":
        return "check by hand; consider outreach"
    return "review"


def _ratio(n, d):
    return None if not d else round(n / d, 4)


def _metrics(ctx: Ctx, bench: dict, hits: list[str], latencies: list[int]) -> dict:
    conn = ctx.conn
    verified = len(bench["opportunities"])
    labels = bench["labels"]
    relevant = sum(1 for lab in labels if lab.get("relevant"))
    # source coverage over the benchmark's employer list
    covered = 0
    for emp in bench["employers"]:
        eid = entities.resolve(conn, emp.get("name"), website=emp.get("website"))
        if eid and one(conn, "SELECT 1 FROM sources WHERE employer_id = ? AND enabled = 1 AND health IN ('ok', "
                       "'unknown', 'degraded')", (eid,)):
            covered += 1
    since = iso(ctx.now - timedelta(days=30))
    checks = one(conn, "SELECT COUNT(*) n, SUM(outcome IN ('ok', 'empty', 'not_modified', 'unchanged', 'deferred')) "
                 "ok FROM source_checks WHERE at >= ?", (since,))
    opps = one(conn, "SELECT COUNT(*) n FROM opportunities WHERE excluded_reason IS NULL")["n"]
    flagged = one(conn, "SELECT COUNT(*) n FROM opportunities WHERE review_flag LIKE 'possible_duplicate:%'")["n"]
    cost = one(conn, "SELECT COALESCE(SUM(cost_usd), 0) c FROM ai_calls WHERE at >= ?", (since,))["c"]
    useful = one(conn, "SELECT COUNT(*) n FROM opportunities WHERE first_seen_at >= ? AND status = 'open' AND "
                 "excluded_reason IS NULL", (since,))["n"]
    elig = [lab for lab in labels if "eligible" in lab]
    return {
        "verified_opportunities": verified,
        "discovery_recall": _ratio(len(hits), verified),
        "discovery_precision": _ratio(relevant, len(labels)),
        "detection_latency_days_median": statistics.median(latencies) if latencies else None,
        "source_coverage": _ratio(covered, len(bench["employers"])),
        "benchmark_employers": len(bench["employers"]),
        "source_reliability_30d": _ratio(checks["ok"] or 0, checks["n"]),
        "possible_duplicate_rate": _ratio(flagged, opps),
        "ai_cost_30d_usd": round(cost, 4),
        "cost_per_new_open_opportunity_30d_usd": None if not useful else round(cost / useful, 4),
        "eligibility_labels": len(elig),
        "notes": {
            "discovery_recall": None if verified else "no verified opportunities in the benchmark yet",
            "discovery_precision": None if labels else "no relevance labels in the benchmark yet",
            "eligibility_accuracy": "evaluated in the browser against your private profile (Sources > Coverage)",
            "recruitment_outcomes": "computed in the browser from your private application records",
        },
    }


def latest(conn) -> dict | None:
    row = one(conn, "SELECT * FROM coverage_evaluations ORDER BY id DESC LIMIT 1")
    if row is None:
        return None
    return {"at": row["at"], "benchmark_version": row["benchmark_version"], "metrics": loads(row["metrics"], {}),
            "misses": loads(row["misses"], [])}
