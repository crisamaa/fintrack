"""The ingestion pipeline: one source check in, transactional updates to postings, opportunities and the source out.

    fetch (connector)  ->  record the check  ->  for each posting: store the raw observation, resolve the employer,
    match or create the opportunity, record facts and requirements (rules first), link its program, queue AI
    enrichment if useful  ->  absence handling (only after a complete, successful check)  ->  source health and the
    next check time  ->  notifications.

Invariants the tests hold this module to:
- A failed check changes no posting or opportunity. Only `complete` successful checks can mark postings absent,
  and an empty board must be empty three checks in a row before its postings count as gone.
- AI-extracted postings must be missing twice before they count as gone (extraction is noisier than an API).
- Re-running the same check is idempotent: no duplicate postings, opportunities or events.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from tracker import entities, facts, jobs, lifecycle, notify, programs, requirements, schedule
from tracker.connectors import FetchResult, RawPosting
from tracker.context import Ctx
from tracker.db import dumps, iso, loads, one, tx
from tracker.http import SourceFailure
from tracker.identity import canonical_url, find_match, opportunity_id, title_key
from tracker import roles
from tracker.taxonomy import classify_sectors, classify_type, intake_year, parse_location

log = logging.getLogger(__name__)

EMPTY_CHECKS_TO_CLOSE = 3
MISSES_TO_CLOSE = {"structured": 1, "jsonld": 1, "import": 1, "ai": 2}
DESCRIPTION_CHARS = 6000


@dataclass
class CheckOutcome:
    outcome: str
    postings: int = 0
    new_postings: int = 0
    new_opportunities: int = 0
    closed: int = 0


def _hash(p: RawPosting) -> str:
    return hashlib.sha256(f"{p.title}\x00{p.location}\x00{p.description}".encode()).hexdigest()[:20]


def _record_check(ctx: Ctx, source_id: str, outcome: str, duration_ms: int, *, http_status=None, postings=None,
                  new_postings=None, error=None) -> None:
    ctx.conn.execute("INSERT INTO source_checks (source_id, run_id, at, duration_ms, outcome, http_status, postings, "
                     "new_postings, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (source_id, ctx.run_id, iso(ctx.now), duration_ms, outcome, http_status, postings, new_postings,
                      (error or "")[:500] or None))


# --- failures ------------------------------------------------------------------------------------------------------

def record_failure(ctx: Ctx, source: dict, failure: SourceFailure, duration_ms: int = 0) -> CheckOutcome:
    """Workflow H: the failure is recorded; previously verified opportunities are left exactly as they were."""
    with tx(ctx.conn):
        failures = source["consecutive_failures"] + 1
        _record_check(ctx, source["id"], failure.kind if failure.kind != "unsafe_url" else "blocked", duration_ms,
                      http_status=failure.http_status, error=str(failure))
        health = schedule.health_after_failure(failure.kind, failures)
        ctx.conn.execute(
            "UPDATE sources SET last_attempt_at = ?, consecutive_failures = ?, checks_total = checks_total + 1, "
            "checks_failed = checks_failed + 1, last_outcome = ?, last_error = ?, health = ?, next_check_at = ?, "
            "updated_at = ? WHERE id = ?",
            (iso(ctx.now), failures, failure.kind, str(failure)[:500], health,
             schedule.next_after_failure(source, failures, failure.kind, ctx.now), iso(ctx.now), source["id"]))
        if health in ("failing", "blocked", "gone") and source.get("health") not in ("failing", "blocked", "gone"):
            notify.source_problem(ctx, source, health, str(failure))
    ctx.stats[f"check_{failure.kind}"] += 1
    return CheckOutcome(failure.kind)


# --- success ---------------------------------------------------------------------------------------------------------

def ingest(ctx: Ctx, source: dict, result: FetchResult, duration_ms: int = 0) -> CheckOutcome:
    with tx(ctx.conn):
        for ref in result.found_sources:
            _, created = entities.register_source(
                ctx.conn, ctx.now, ref, employer_id=source.get("employer_id"), discovery_method="homepage_link",
                evidence=f"linked from {source['url']}", association=source.get("association", "verified"),
                priority=source.get("priority", 2))
            ctx.stats["sources_found"] += created
        if result.not_modified:
            out = CheckOutcome("not_modified")
            _confirm_present(ctx, source)
            _record_check(ctx, source["id"], "not_modified", duration_ms, http_status=result.http_status)
        elif result.needs_ai:
            out = CheckOutcome("deferred")
            if result.page_text:
                ctx.page_cache[source["id"]] = result.page_text
            jobs.enqueue(ctx.conn, ctx.now, "extract_page", source["id"], {"hash": result.content_hash}, refresh=True)
            _record_check(ctx, source["id"], "deferred", duration_ms, http_status=result.http_status)
        else:
            out = apply_postings(ctx, source, result.postings, complete=result.complete,
                                 total_listed=result.total_listed)
            _record_check(ctx, source["id"], out.outcome, duration_ms, http_status=result.http_status,
                          postings=out.postings, new_postings=out.new_postings)
        content_hash = result.content_hash if not result.needs_ai else source.get("content_hash")
        fresh = dict(source, **dict(one(ctx.conn, "SELECT * FROM sources WHERE id = ?", (source["id"],))))
        ctx.conn.execute(
            "UPDATE sources SET last_attempt_at = ?, last_success_at = ?, consecutive_failures = 0, "
            "checks_total = checks_total + 1, last_outcome = ?, last_error = NULL, health = 'ok', etag = ?, "
            "last_modified = ?, content_hash = ?, last_change_at = CASE WHEN ? THEN ? ELSE last_change_at END, "
            "next_check_at = ?, updated_at = ? WHERE id = ?",
            (iso(ctx.now), iso(ctx.now), out.outcome, result.etag, result.last_modified, content_hash,
             1 if out.new_postings or out.closed else 0, iso(ctx.now),
             schedule.next_after_success(ctx.conn, fresh, ctx.now), iso(ctx.now), source["id"]))
    ctx.stats["checks_ok"] += 1
    ctx.stats["new_opportunities"] += out.new_opportunities
    return out


def _confirm_present(ctx: Ctx, source: dict) -> None:
    """The source answered 'unchanged': everything it listed is still listed."""
    for r in ctx.conn.execute("SELECT opportunity_id FROM source_postings WHERE source_id = ? AND present = 1 "
                              "AND opportunity_id IS NOT NULL", (source["id"],)).fetchall():
        _seen_open(ctx, r["opportunity_id"], source)
    ctx.conn.execute("UPDATE source_postings SET last_seen_at = ? WHERE source_id = ? AND present = 1",
                     (iso(ctx.now), source["id"]))


def _seen_open(ctx: Ctx, opp_id: str, source: dict) -> None:
    opp = one(ctx.conn, "SELECT status, closes_on FROM opportunities WHERE id = ?", (opp_id,))
    ctx.conn.execute("UPDATE opportunities SET last_seen_at = ? WHERE id = ?", (iso(ctx.now), opp_id))
    if not _employer_controlled(source):
        return
    expired = opp["closes_on"] and opp["closes_on"] < ctx.today.isoformat()
    if opp["status"] in ("pending", "unavailable", "unknown", "closed") and not expired:
        lifecycle.transition(ctx.conn, ctx.now, opp_id, "open", f"listed by {source['id']}", source["id"])
    elif opp["status"] == "expired" and not expired:
        lifecycle.transition(ctx.conn, ctx.now, opp_id, "open", f"closing date moved; listed by {source['id']}",
                             source["id"])
    ctx.conn.execute("UPDATE opportunities SET last_verified_at = ? WHERE id = ?", (iso(ctx.now), opp_id))


def _employer_controlled(source: dict) -> bool:
    return source.get("association") == "verified" and source.get("category") not in ("aggregator", "import")


def apply_postings(ctx: Ctx, source: dict, postings: list[RawPosting], *, complete: bool,
                   total_listed: int | None = None) -> CheckOutcome:
    """Store postings from one successful read of a source and update opportunities. Call inside a transaction."""
    conn = ctx.conn
    out = CheckOutcome("ok" if postings else "empty", postings=len(postings))
    seen: set[str] = set()
    for p in postings:
        if not p.url.startswith(("http://", "https://")) or not p.title.strip():
            continue
        seen.add(p.external_id)
        new_posting, opp_new = _apply_one(ctx, source, p)
        out.new_postings += new_posting
        out.new_opportunities += opp_new
    if complete:
        out.closed = _handle_absent(ctx, source, seen, listed=bool(postings) or bool(total_listed))
    if out.new_postings:
        conn.execute("UPDATE sources SET postings_total = postings_total + ? WHERE id = ?",
                     (out.new_postings, source["id"]))
    return out


def _apply_one(ctx: Ctx, source: dict, p: RawPosting) -> tuple[int, int]:
    conn, now = ctx.conn, ctx.now
    h = _hash(p)
    row = one(conn, "SELECT id, content_hash, opportunity_id FROM source_postings WHERE source_id = ? AND "
              "external_id = ?", (source["id"], p.external_id))
    desc = p.description[:DESCRIPTION_CHARS]
    if row is None:
        conn.execute("INSERT INTO source_postings (source_id, external_id, title, url, location_text, description, "
                     "content_hash, published_at, structured, extraction, present, miss_count, first_seen_at, "
                     "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
                     (source["id"], p.external_id, p.title, p.url, p.location, desc, h, p.published_at,
                      dumps(p.structured), p.extraction, iso(now), iso(now)))
        new_posting, changed = 1, True
    else:
        changed = row["content_hash"] != h
        conn.execute("UPDATE source_postings SET title = ?, url = ?, location_text = ?, description = ?, "
                     "content_hash = ?, published_at = COALESCE(?, published_at), structured = ?, present = 1, "
                     "miss_count = 0, last_seen_at = ? WHERE id = ?",
                     (p.title, p.url, p.location, desc, h, p.published_at, dumps(p.structured), iso(now), row["id"]))
        new_posting = 0
    employer_id = source.get("employer_id") or _employer_for_posting(ctx, source, p)
    if not employer_id:
        return new_posting, 0  # kept as an unlinked observation until discovery registers the employer
    employer_id = entities.follow_merges(conn, employer_id)

    city, countries = parse_location(p.location)
    year = intake_year(p.title)
    opp_id, how, possible = find_match(conn, employer_id=employer_id, source_id=source["id"],
                                       external_id=p.external_id, url=p.url, title=p.title, city=city,
                                       countries=countries, year=year,
                                       requisition_id=p.structured.get("requisition_id"))
    created = 0
    if opp_id is None:
        opp_id = _create(ctx, source, employer_id, p, possible)
        created = 1
    elif how == "cross_source_title":
        conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, source_id, note) VALUES "
                     "(?, ?, 'merged', ?, ?)", (opp_id, iso(now), source["id"],
                                                f"same role also listed at {p.url}"))
    conn.execute("UPDATE source_postings SET opportunity_id = ? WHERE source_id = ? AND external_id = ?",
                 (opp_id, source["id"], p.external_id))
    if created or changed:
        _record_facts(ctx, source, opp_id, p, city, countries, year)
        requirements.store(conn, now, opp_id, requirements.extract(p.description), "rule")
        refresh_exclusion(ctx, opp_id)
        programs.link_opportunity(ctx, opp_id)
        if p.description.strip():
            jobs.enqueue(conn, now, "enrich", opp_id, {"hash": h}, refresh=True)
        else:
            conn.execute("UPDATE opportunities SET enrichment = 'skipped' WHERE id = ? AND enrichment = 'pending'",
                         (opp_id,))
    _seen_open(ctx, opp_id, source)
    if not _employer_controlled(source):
        _check_monitoring_gap(ctx, employer_id, opp_id, source)
    else:
        resolve_gaps(ctx, opp_id)
    if created:
        if not _employer_controlled(source):
            jobs.enqueue(conn, now, "verify", opp_id, {"url": p.url})
        notify.new_opportunity(ctx, opp_id)
    return new_posting, created


def _create(ctx: Ctx, source: dict, employer_id: str, p: RawPosting, possible: str | None) -> str:
    conn, now = ctx.conn, ctx.now
    canon = canonical_url(p.url)
    opp_id = opportunity_id(employer_id, canon)
    while one(conn, "SELECT 1 FROM opportunities WHERE id = ?", (opp_id,)):
        opp_id = hashlib.sha1(f"{opp_id}|{p.external_id}".encode()).hexdigest()[:16]
    via = "monitoring" if _employer_controlled(source) else ("import" if source.get("category") == "import"
                                                              else "search")
    conn.execute("INSERT INTO opportunities (id, employer_id, title, canonical_url, title_key, internship_type, "
                 "description, status, discovered_via, first_seen_at, last_seen_at, review_flag, created_at, "
                 "updated_at) VALUES (?, ?, ?, ?, ?, 'unknown', ?, 'pending', ?, ?, ?, ?, ?, ?)",
                 (opp_id, employer_id, p.title, canon, title_key(p.title), p.description[:3000], via, iso(now),
                  iso(now), f"possible_duplicate:{possible}" if possible else None, iso(now), iso(now)))
    conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, to_status, source_id, note) VALUES "
                 "(?, ?, 'discovered', 'pending', ?, ?)", (opp_id, iso(now), source["id"], p.url))
    return opp_id


def _record_facts(ctx: Ctx, source: dict, opp_id: str, p: RawPosting, city, countries, year) -> None:
    conn, now, sid = ctx.conn, ctx.now, source["id"]
    method = {"structured": "structured", "jsonld": "jsonld", "ai": "ai", "import": "rule"}[p.extraction]
    facts.set_fact(conn, now, opp_id, "title", p.title, method, source_id=sid)
    conn.execute("UPDATE opportunities SET title_key = ? WHERE id = ?", (title_key(p.title), opp_id))
    if p.published_at:
        facts.set_fact(conn, now, opp_id, "published_at", p.published_at, method, source_id=sid)
    if p.structured.get("valid_through"):
        facts.set_fact(conn, now, opp_id, "closes_on", p.structured["valid_through"], method, source_id=sid,
                       evidence="validThrough in the posting's structured data")
    if city:
        facts.set_fact(conn, now, opp_id, "city", city, "rule", source_id=sid, evidence=p.location)
    if countries:
        facts.set_fact(conn, now, opp_id, "countries", countries, "rule", source_id=sid, evidence=p.location)
    t = classify_type(p.title + " " + " ".join(str(v) for v in p.structured.values() if isinstance(v, str)))
    facts.set_fact(conn, now, opp_id, "internship_type", t, "rule", source_id=sid, evidence=p.title)
    if year:
        facts.set_fact(conn, now, opp_id, "intake_year", year, "rule", source_id=sid, evidence=p.title)
    dept = " ".join(str(x) for x in (p.structured.get("departments") or [p.structured.get("department") or ""]) if x)
    sectors = classify_sectors(f"{p.title} {dept}")
    if sectors:
        facts.set_fact(conn, now, opp_id, "sectors", sectors, "rule", source_id=sid, evidence=f"{p.title} {dept}".strip())
    langs = [code for code, _ in requirements.required_languages(p.description)]
    if langs:
        facts.set_fact(conn, now, opp_id, "languages", langs, "rule", source_id=sid)
    roles.store(conn, now, opp_id, department=dept or None)  # no department stated: keep the stored one


def refresh_exclusion(ctx: Ctx, opp_id: str) -> None:
    """Exclude (not delete) opportunities based outside the countries of interest that do not offer sponsorship."""
    row = one(ctx.conn, "SELECT countries, excluded_reason FROM opportunities WHERE id = ?", (opp_id,))
    if row["excluded_reason"] == "not_internship":
        return
    countries = loads(row["countries"], [])
    sponsor = one(ctx.conn, "SELECT 1 FROM opportunity_requirements WHERE opportunity_id = ? AND kind = 'sponsorship' "
                  "AND value = ?", (opp_id, dumps({"available": True})))
    outside = bool(countries) and not set(countries) & ctx.config.countries and sponsor is None
    ctx.conn.execute("UPDATE opportunities SET excluded_reason = ? WHERE id = ?",
                     ("outside_countries" if outside else None, opp_id))


def _employer_for_posting(ctx: Ctx, source: dict, p: RawPosting) -> str | None:
    """Aggregator and search postings: the hiring organisation named in the posting, if we know it."""
    name = p.structured.get("hiring_organization")
    site = p.structured.get("hiring_organization_url")
    found = entities.resolve(ctx.conn, name, website=site)
    if found is None and name:
        city, countries = parse_location(p.location)
        entities.add_candidate(ctx.conn, ctx.now, name=name, origin="posting", website=site,
                               country=countries[0] if countries else None, evidence_url=p.url,
                               evidence=f"hiring organisation of '{p.title}' on {source['id']}")
    return found


def _check_monitoring_gap(ctx: Ctx, employer_id: str, opp_id: str, source: dict) -> None:
    """Workflow C: an opportunity found outside the employer's own monitored sources reveals a coverage gap."""
    conn = ctx.conn
    monitored = one(conn, "SELECT 1 FROM source_postings sp JOIN sources s ON s.id = sp.source_id WHERE "
                    "sp.opportunity_id = ? AND sp.present = 1 AND s.association = 'verified' AND s.category NOT IN "
                    "('aggregator', 'import')", (opp_id,))
    if monitored:
        return
    has_sources = one(conn, "SELECT COUNT(*) n FROM sources WHERE employer_id = ? AND enabled = 1 AND category NOT IN "
                      "('aggregator', 'import')", (employer_id,))["n"]
    cause = "missed_by_monitoring" if has_sources else "missing_source"
    cur = conn.execute("INSERT OR IGNORE INTO coverage_gaps (employer_id, opportunity_id, cause, detail, detected_by, "
                       "detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (employer_id, opp_id, cause, f"found via {source['id']} but not on the employer's "
                        f"{'monitored sources' if has_sources else 'own sources (none registered)'}",
                        "search", iso(ctx.now)))
    if cur.rowcount:
        jobs.enqueue(conn, ctx.now, "discover_sources", employer_id, {"reason": cause, "opportunity": opp_id},
                     refresh=True)
        ctx.stats["coverage_gaps"] += 1


def resolve_gaps(ctx: Ctx, opp_id: str) -> None:
    ctx.conn.execute("UPDATE coverage_gaps SET resolved_at = ?, resolution = 'now listed by a monitored source' "
                     "WHERE opportunity_id = ? AND resolved_at IS NULL", (iso(ctx.now), opp_id))


def _handle_absent(ctx: Ctx, source: dict, seen: set[str], *, listed: bool) -> int:
    conn, now = ctx.conn, ctx.now
    present = conn.execute("SELECT id, external_id, opportunity_id, extraction, miss_count FROM source_postings "
                           "WHERE source_id = ? AND present = 1", (source["id"],)).fetchall()
    missing = [r for r in present if r["external_id"] not in seen]
    if not listed and present:
        streak = source.get("zero_streak", 0) + 1
        conn.execute("UPDATE sources SET zero_streak = ? WHERE id = ?", (streak, source["id"]))
        if streak < EMPTY_CHECKS_TO_CLOSE:
            return 0  # an empty board may be a broken board: wait for more empty checks
    else:
        conn.execute("UPDATE sources SET zero_streak = 0 WHERE id = ?", (source["id"],))
    closed = 0
    for r in missing:
        misses = r["miss_count"] + 1
        gone = misses >= MISSES_TO_CLOSE.get(r["extraction"], 1)
        conn.execute("UPDATE source_postings SET miss_count = ?, present = ? WHERE id = ?",
                     (misses, 0 if gone else 1, r["id"]))
        if not gone or not r["opportunity_id"]:
            continue
        still = one(conn, "SELECT 1 FROM source_postings WHERE opportunity_id = ? AND present = 1",
                    (r["opportunity_id"],))
        opp = one(conn, "SELECT status FROM opportunities WHERE id = ?", (r["opportunity_id"],))
        if still is None and opp["status"] in ("open", "pending", "unavailable", "unknown"):
            lifecycle.transition(conn, now, r["opportunity_id"], "closed",
                                 f"no longer listed by {source['id']} (complete check)", source["id"])
            closed += 1
    return closed


# --- maintenance -------------------------------------------------------------------------------------------------

UNAVAILABLE_AFTER = timedelta(days=7)


def maintain(ctx: Ctx) -> dict[str, int]:
    """Expire past closing dates; mark opportunities whose sources all fail as unavailable (never closed)."""
    conn, now = ctx.conn, ctx.now
    counts = {"expired": 0, "unavailable": 0}
    with tx(conn):
        for r in conn.execute("SELECT id, closes_on FROM opportunities WHERE status IN ('open', 'pending', "
                              "'unavailable', 'unknown') AND closes_on IS NOT NULL AND closes_on < ?",
                              (ctx.today.isoformat(),)).fetchall():
            lifecycle.transition(conn, now, r["id"], "expired", f"closing date {r['closes_on']} has passed")
            counts["expired"] += 1
        cutoff = iso(now - UNAVAILABLE_AFTER)
        for r in conn.execute(
                "SELECT o.id FROM opportunities o WHERE o.status = 'open' AND COALESCE(o.last_verified_at, "
                "o.first_seen_at) < ? AND NOT EXISTS (SELECT 1 FROM source_postings sp JOIN sources s ON "
                "s.id = sp.source_id WHERE sp.opportunity_id = o.id AND sp.present = 1 AND s.health = 'ok')",
                (cutoff,)).fetchall():
            lifecycle.transition(conn, now, r["id"], "unavailable",
                                 "not confirmed for 7 days because its sources are failing")
            counts["unavailable"] += 1
    return counts
