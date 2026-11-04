"""Opportunity creation (Workflow G): employers worth approaching although they advertise nothing right now.

A prospect is never a vacancy. Scores are sums of named, explainable factors, not predictions of success.
Research is grounded: the firm's own pages are fetched, the model writes notes, and a note is kept only if it cites
one of those fetched URLs and its quote occurs on that page. Contacts and outreach drafts are personal and are kept
in the browser, never here; this module stores only facts about the firm.

Research runs for employers listed in firms/research.yaml (requested by the user, e.g. by editing the file on
GitHub or via the workflow's research input) and for the top-scoring prospects, within research_per_run.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from tracker import prompts
from tracker.ai import AIUnusable, quote_in
from tracker.context import Ctx
from tracker.db import dumps, iso, one, parse_iso, tx
from tracker.http import SourceFailure, links, page_text
from tracker.identity import canonical_url
from tracker.names import registrable_domain

log = logging.getLogger(__name__)
RESEARCH_MAX_AGE = timedelta(days=120)
_USEFUL_LINK = ("about", "ueber-uns", "uber-uns", "a-propos", "nosotros", "quienes", "team", "career", "karriere",
                "carriere", "jobs", "join", "students", "graduates", "contact")


def score_all(ctx: Ctx) -> int:
    """(Re)score every active employer with no open, listed opportunity."""
    conn = ctx.conn
    n = 0
    with tx(conn):
        rows = conn.execute(
            "SELECT e.* FROM employers e WHERE e.status = 'active' AND NOT EXISTS (SELECT 1 FROM opportunities o "
            "WHERE o.employer_id = e.id AND o.status IN ('open', 'pending') AND o.excluded_reason IS NULL)").fetchall()
        live = {r["id"] for r in rows}
        conn.execute(f"DELETE FROM prospects WHERE status = 'suggested' AND employer_id NOT IN "
                     f"({','.join('?' * len(live)) or 'NULL'})", tuple(live))
        for e in rows:
            reasons = _reasons(ctx, e)
            score = sum(r["points"] for r in reasons)
            if score <= 0:
                continue
            conn.execute("INSERT INTO prospects (employer_id, status, score, reasons, updated_at) VALUES "
                         "(?, 'suggested', ?, ?, ?) ON CONFLICT(employer_id) DO UPDATE SET score = excluded.score, "
                         "reasons = excluded.reasons, updated_at = excluded.updated_at",
                         (e["id"], score, dumps(reasons), iso(ctx.now)))
            n += 1
    return n


def _reasons(ctx: Ctx, e) -> list[dict]:
    conn = ctx.conn
    out = []
    past = one(conn, "SELECT COUNT(*) n, MAX(first_seen_at) last FROM opportunities WHERE employer_id = ? AND "
               "excluded_reason IS NULL", (e["id"],))
    if past["n"]:
        out.append({"factor": "hired_interns_before", "points": 3,
                    "detail": f"{past['n']} internship posting(s) recorded, last seen {past['last'][:10]}"})
    progs = one(conn, "SELECT COUNT(*) n FROM programs WHERE employer_id = ?", (e["id"],))["n"]
    if progs:
        out.append({"factor": "recurring_program", "points": 2, "detail": f"{progs} recurring program(s) known"})
    if e["size_hint"] in ("boutique", "mid"):
        out.append({"factor": "small_firm", "points": 2,
                    "detail": "smaller firms often hire interns without advertising"})
    sectors = [r["sector"] for r in conn.execute("SELECT sector FROM employer_sectors WHERE employer_id = ?",
                                                  (e["id"],))]
    wanted = ctx.config.notify_sectors
    if sectors and (not wanted or set(sectors) & set(wanted)):
        out.append({"factor": "sector_fit", "points": 1, "detail": ", ".join(sectors[:3])})
    if e["hq_country"] in ("CH", "PE"):
        out.append({"factor": "home_market", "points": 1, "detail": f"based in {e['hq_country']}"})
    if e["access_note"]:
        out.append({"factor": "not_monitorable", "points": 1,
                    "detail": "its careers site cannot be monitored automatically; direct contact is the way in"})
    if e["website"]:
        out.append({"factor": "researchable", "points": 1, "detail": "official website known"})
    has_source = one(conn, "SELECT 1 FROM sources WHERE employer_id = ? AND enabled = 1", (e["id"],))
    if not has_source:
        out.append({"factor": "no_recruitment_channel", "points": 1,
                    "detail": "no public recruitment source found: likely recruits informally"})
    return out


def requested(root: Path) -> list[str]:
    path = Path(root) / "firms" / "research.yaml"
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or []
    return [str(x) for x in data] if isinstance(data, list) else []


def research_due(ctx: Ctx) -> int:
    """Queue research jobs: requested firms first, then the top prospects not researched recently."""
    from tracker import jobs
    n = 0
    with tx(ctx.conn):
        ids = requested(ctx.config.root)
        for eid in ids:
            if one(ctx.conn, "SELECT 1 FROM employers WHERE id = ?", (eid,)):
                ctx.conn.execute("UPDATE prospects SET research_requested = 1 WHERE employer_id = ?", (eid,))
                n += jobs.enqueue(ctx.conn, ctx.now, "research", eid, {"requested": True})
        top = ctx.conn.execute("SELECT p.employer_id, p.researched_at FROM prospects p JOIN employers e ON "
                               "e.id = p.employer_id WHERE p.status != 'dismissed' AND e.website IS NOT NULL ORDER BY "
                               "p.score DESC, p.employer_id LIMIT ?", (ctx.config.research_per_run * 3,)).fetchall()
        queued = 0
        for r in top:
            last = parse_iso(r["researched_at"])
            if last and ctx.now - last < RESEARCH_MAX_AGE:
                continue
            if queued >= ctx.config.research_per_run:
                break
            queued += jobs.enqueue(ctx.conn, ctx.now, "research", r["employer_id"], {}, refresh=bool(last))
        n += queued
    return n


def research(ctx: Ctx, employer_id: str) -> bool:
    """Fetch the firm's own pages and store grounded notes. Returns False when the AI could not run."""
    e = one(ctx.conn, "SELECT * FROM employers WHERE id = ?", (employer_id,))
    if e is None or not e["website"]:
        return True
    if ctx.ai is None or not ctx.ai.available:
        return False
    pages: dict[str, str] = {}
    try:
        resp = ctx.http.get(e["website"])
    except SourceFailure as err:
        log.info("research %s: homepage unreachable: %s", employer_id, err)
        return True
    pages[resp.url] = page_text(resp.text)
    domain = registrable_domain(resp.url)
    for url, label in links(resp.text, resp.url):
        if len(pages) >= 4:
            break
        path = urlsplit(url).path.lower()
        if url in pages or registrable_domain(url) != domain or not any(k in path or k in label.lower()
                                                                         for k in _USEFUL_LINK):
            continue
        try:
            sub = ctx.http.get(url)
        except SourceFailure:
            continue
        pages[sub.url] = page_text(sub.text)
    content = "\n\n".join(f"<page url=\"{u}\">\n{t[:12000]}\n</page>" for u, t in pages.items())
    try:
        r = ctx.ai.run(*prompts.RESEARCH, prompts.RESEARCH_SYSTEM, f"Firm: {e['name']}\n\n{content}",
                       prompts.Research)
    except AIUnusable as err:
        log.info("research %s: unusable reply: %s", employer_id, err)
        return True
    if r is None:
        return False
    kept = 0
    by_canon = {canonical_url(u): t for u, t in pages.items()}
    for note in r.notes:
        page = by_canon.get(canonical_url(note.source_url)) if note.source_url.startswith("http") else None
        if page is None or not quote_in(note.quote, page):
            ctx.stats["research_notes_discarded"] += 1
            continue
        ctx.conn.execute("INSERT OR IGNORE INTO employer_intel (employer_id, kind, text, evidence, source_url, method, "
                         "recorded_at) VALUES (?, ?, ?, ?, ?, 'ai', ?)",
                         (employer_id, note.kind, note.text[:600], note.quote[:400], note.source_url, iso(ctx.now)))
        kept += 1
    channel = r.application_channel if r.application_channel and quote_in(r.application_channel, content) else None
    ctx.conn.execute(
        "INSERT INTO prospects (employer_id, status, score, reasons, summary, application_channel, researched_at, "
        "model, prompt_version, updated_at) VALUES (?, 'researched', 0, '[]', ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(employer_id) DO UPDATE SET status = CASE WHEN prospects.status = 'dismissed' THEN 'dismissed' "
        "ELSE 'researched' END, summary = excluded.summary, application_channel = excluded.application_channel, "
        "researched_at = excluded.researched_at, model = excluded.model, prompt_version = excluded.prompt_version, "
        "updated_at = excluded.updated_at",
        (employer_id, r.summary[:800] if kept else None, channel, iso(ctx.now), ctx.ai.provider.model,
         "/".join(prompts.RESEARCH), iso(ctx.now)))
    ctx.stats["research_notes"] += kept
    return True
