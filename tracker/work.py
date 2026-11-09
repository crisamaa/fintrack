"""Handlers for queued jobs, and the loops that collect due sources and drain the queue.

Each handler returns normally (job done), raises Defer (retry later without counting an attempt: budget stop or
AI outage), or raises any other exception (counted attempt, exponential backoff, dead after max_attempts).
Collection never waits on AI: page sources that need the model queue an extract_page job and move on.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from urllib.parse import urljoin, urlsplit

from tracker import connectors, entities, facts, ingest, jobs, lifecycle, programs, prompts, requirements, roles
from tracker.ai import AIAccountError, quote_in
from tracker.connectors import RawPosting, SourceRef
from tracker.connectors.page import fallback_external_id, jsonld_postings
from tracker.context import Ctx
from tracker.db import iso, loads, one, tx
from tracker.http import SourceFailure, links, text_hash
from tracker.names import name_tokens, registrable_domain, same_firm
from tracker.taxonomy import COUNTRIES_OF_INTEREST, INTERNSHIP_TYPES, fold
from tracker.titles import looks_like_internship

log = logging.getLogger(__name__)
CHUNK_CHARS = 40_000


class Defer(Exception):
    """Not the job's fault (budget, outage): retry later without counting an attempt."""


def _ai_or_defer(ctx: Ctx):
    if ctx.ai is None or not ctx.ai.available:
        raise Defer(f"AI unavailable ({ctx.ai.blocked_reason if ctx.ai else 'not configured'})")
    return ctx.ai


# --- collection ----------------------------------------------------------------------------------------------------

def collect(ctx: Ctx, limit: int | None = None) -> int:
    from tracker.schedule import due_sources
    checked = 0
    for src in due_sources(ctx.conn, ctx.now, limit or ctx.config.max_source_checks):
        if not ctx.time_left():
            ctx.stats["sources_left_for_next_run"] += 1
            continue
        source = dict(src)
        t0 = time.monotonic()
        try:
            result = connectors.get(source["kind"]).fetch(source, ctx.http)
        except SourceFailure as f:
            ingest.record_failure(ctx, source, f, int((time.monotonic() - t0) * 1000))
        except Exception as e:  # a connector bug must not stop the run, but must be visible
            log.exception("connector %s crashed on %s", source["kind"], source["id"])
            ctx.errors.append(f"{source['id']}: {e!r}")
            ingest.record_failure(ctx, source, SourceFailure("format_changed", f"connector error: {e!r}"),
                                  int((time.monotonic() - t0) * 1000))
        else:
            ingest.ingest(ctx, source, result, int((time.monotonic() - t0) * 1000))
        checked += 1
    return checked


HANDLERS: dict[str, object] = {}
AI_KINDS = {"extract_page", "enrich", "research"}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def drain(ctx: Ctx, kinds: list[str] | None = None, limit: int | None = None) -> dict[str, int]:
    """Run due jobs. Deferred AI jobs stay queued; one broken job never stops the others."""
    counts = {"done": 0, "deferred": 0, "failed": 0, "dead": 0}
    kinds = kinds or list(HANDLERS)
    for row in jobs.due(ctx.conn, ctx.now, kinds, limit or ctx.config.max_jobs):
        if not ctx.time_left():
            break
        jobs.claim(ctx.conn, ctx.now, row["id"])
        try:
            with tx(ctx.conn):
                HANDLERS[row["kind"]](ctx, row["key"], loads(row["payload"], {}))
                jobs.done(ctx.conn, ctx.now, row["id"])
            counts["done"] += 1
        except Defer as d:
            jobs.defer(ctx.conn, ctx.now, row["id"], str(d), timedelta(hours=6))
            counts["deferred"] += 1
        except AIAccountError as e:
            jobs.defer(ctx.conn, ctx.now, row["id"], f"AI account error: {e}", timedelta(hours=12))
            counts["deferred"] += 1
            ctx.errors.append(f"AI account error: {e}")
        except Exception as e:
            status = jobs.fail(ctx.conn, ctx.now, row["id"], f"{type(e).__name__}: {e}")
            counts["dead" if status == "dead" else "failed"] += 1
            if status == "dead" and row["kind"] == "enrich":
                ctx.conn.execute("UPDATE opportunities SET enrichment = 'failed' WHERE id = ?", (row["key"],))
            log.warning("job %s %s failed: %s", row["kind"], row["key"], e)
        finally:
            if ctx.ai is not None:
                ctx.ai.flush()  # outside the job's transaction: billed usage survives a rollback
    for k, v in counts.items():
        ctx.stats[f"jobs_{k}"] += v
    return counts


# --- extract_page -------------------------------------------------------------------------------------------------

@handler("extract_page")
def extract_page(ctx: Ctx, source_id: str, payload: dict) -> None:
    src = one(ctx.conn, "SELECT * FROM sources WHERE id = ?", (source_id,))
    if src is None or not src["enabled"]:
        return
    ai = _ai_or_defer(ctx)
    text = ctx.page_cache.get(source_id)
    if text is None:
        _, text = ctx.http.get_text(src["url"])
    h = text_hash(text)
    postings: list[RawPosting] = []
    for i in range(0, len(text), CHUNK_CHARS):
        chunk = text[i:i + CHUNK_CHARS]
        parsed = ai.run(*prompts.EXTRACT_PAGE, prompts.EXTRACT_PAGE_SYSTEM,
                        f"Careers page at {src['url']}.\n\n<page_text>\n{chunk}\n</page_text>", prompts.PostingList)
        if parsed is None:
            raise Defer("AI budget or outage")
        for p in parsed.postings:
            title = " ".join(p.title.split())
            # Valid JSON is not evidence: a posting whose title is not on the page is discarded.
            if not title or not quote_in(title, chunk):
                ctx.stats["ai_postings_discarded"] += 1
                continue
            if not (looks_like_internship(title) or re.search(r"graduate|insight|programme|program", title, re.I)):
                continue
            url = _resolve(p.url, src["url"])
            ext = url if url and url.split("#")[0] != src["url"].split("#")[0] else None
            postings.append(RawPosting(
                external_id=ext or fallback_external_id(src["url"], title, p.location),
                title=title, url=url or f"{src['url'].split('#')[0]}#{fallback_external_id(src['url'], title, p.location)[:10]}",
                location=p.location, description=p.description, extraction="ai"))
    ingest.apply_postings(ctx, dict(src), postings, complete=True)
    ctx.conn.execute("UPDATE sources SET content_hash = ?, last_change_at = ? WHERE id = ?",
                     (h, iso(ctx.now), source_id))
    if src["category"] in ("early_careers", "program"):
        _program_page(ctx, dict(src), text)


def _resolve(url: str, base: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    try:
        candidate = urljoin(base, url)
    except ValueError:
        return None
    parts = urlsplit(candidate)
    return candidate if parts.scheme in ("http", "https") and parts.netloc else None


def _program_page(ctx: Ctx, src: dict, text: str) -> None:
    """Programs and application dates printed on an early-careers page (Workflow D, Channel E)."""
    if not src.get("employer_id"):
        return
    parsed = ctx.ai.run(*prompts.PROGRAM_PAGE, prompts.PROGRAM_PAGE_SYSTEM,
                        f"Page at {src['url']}.\n\n<page_text>\n{text[:CHUNK_CHARS]}\n</page_text>",
                        prompts.ProgramList)
    if parsed is None:
        return
    from tracker.taxonomy import classify_sectors, parse_location
    for prog in parsed.programs:
        ptype = prog.program_type if prog.program_type in INTERNSHIP_TYPES else "unknown"
        if ptype not in programs.RECURRING_TYPES or not quote_in(prog.name, text):
            continue
        sectors = classify_sectors(f"{prog.name} {prog.division or ''}")
        _, countries = parse_location(prog.location or "")
        pid = programs.ensure_program(ctx, src["employer_id"], ptype, sectors[0] if sectors else None,
                                      countries[0] if countries else None, discovered_via="program_page",
                                      official_url=src["url"])
        if prog.eligibility and quote_in(prog.eligibility[:80], text):
            ctx.conn.execute("UPDATE programs SET eligibility_text = ? WHERE id = ?", (prog.eligibility[:500], pid))
        for d in prog.dates:
            try:
                day = date.fromisoformat(d.date)
            except ValueError:
                continue
            if not quote_in(d.quote, text):
                continue  # a date not printed on the page is never recorded
            year = d.intake_year or (day.year + 1 if ptype in ("summer", "graduate") and day.month >= 7 else day.year)
            kwargs = {"opens_on": day.isoformat(), "closes_on": None} if d.which == "opens" else \
                {"opens_on": None, "closes_on": day.isoformat()}
            programs.record_window(ctx, pid, year, "confirmed", source_url=src["url"],
                                   evidence=("(earlier cycle) " if d.is_past else "") + d.quote[:300], **kwargs)


# --- enrich -------------------------------------------------------------------------------------------------------

@handler("enrich")
def enrich(ctx: Ctx, opp_id: str, payload: dict) -> None:
    o = one(ctx.conn, "SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    if o is None:
        return
    sp = one(ctx.conn, "SELECT * FROM source_postings WHERE opportunity_id = ? ORDER BY LENGTH(description) DESC "
             "LIMIT 1", (opp_id,))
    text = (sp["description"] if sp else "") or o["description"]
    if not text.strip():
        ctx.conn.execute("UPDATE opportunities SET enrichment = 'skipped' WHERE id = ?", (opp_id,))
        return
    ai = _ai_or_defer(ctx)
    content = (f"<posting>\nTitle: {o['title']}\nLocation: {sp['location_text'] if sp else ''}\n"
               f"Description: {text}\n</posting>")
    e = ai.run(*prompts.ENRICH, prompts.ENRICH_SYSTEM, content, prompts.Enrichment)  # AIUnusable: retried, then dead
    if e is None:
        raise Defer("AI budget or outage")
    apply_enrichment(ctx, opp_id, e, text, sp["source_id"] if sp else None)


def apply_enrichment(ctx: Ctx, opp_id: str, e: prompts.Enrichment, text: str, source_id: str | None) -> None:
    conn, now = ctx.conn, ctx.now
    model = ctx.ai.provider.model if ctx.ai and ctx.ai.provider else None
    kw = dict(source_id=source_id, model=model, prompt_version="/".join(prompts.ENRICH))
    countries = [c.strip().upper() for c in e.countries if re.fullmatch(r"[A-Za-z]{2}", c.strip())]
    countries = ["GB" if c == "UK" else c for c in countries]
    facts.set_fact(conn, now, opp_id, "countries", list(dict.fromkeys(countries)), "ai", **kw)
    if e.city:
        facts.set_fact(conn, now, opp_id, "city", e.city.strip()[:80], "ai", **kw)
    if e.internship_type in INTERNSHIP_TYPES:
        facts.set_fact(conn, now, opp_id, "internship_type", e.internship_type, "ai", **kw)
    if e.intake_year and 2020 <= e.intake_year <= 2040:
        facts.set_fact(conn, now, opp_id, "intake_year", e.intake_year, "ai", **kw)
    if e.closes_on and quote_in(e.closes_quote, text):
        try:
            facts.set_fact(conn, now, opp_id, "closes_on", date.fromisoformat(e.closes_on).isoformat(), "ai",
                           evidence=e.closes_quote, **kw)
        except ValueError:
            pass
    langs = [code.lower() for code in e.languages if re.fullmatch(r"[A-Za-z]{2}", code)]
    if langs:
        facts.set_fact(conn, now, opp_id, "languages", list(dict.fromkeys(langs)), "ai", **kw)
    reqs = []
    for r in e.requirements:
        if not quote_in(r.quote, text):
            ctx.stats["ai_requirements_discarded"] += 1
            continue
        value = _req_value(r)
        if value is not None:
            reqs.append((r.kind, value, r.quote[:300]))
    requirements.store(conn, now, opp_id, reqs, "ai")
    if e.role_function and quote_in(e.role_quote, text):
        roles.apply_ai(conn, now, opp_id, e.role_function, e.role_quote[:200], model)
    conn.execute("UPDATE opportunities SET enrichment = 'done', excluded_reason = CASE WHEN ? THEN NULL ELSE "
                 "'not_internship' END WHERE id = ?", (1 if e.is_internship else 0, opp_id))
    if e.is_internship:
        ingest.refresh_exclusion(ctx, opp_id)
        programs.link_opportunity(ctx, opp_id)


def _req_value(r: prompts.ExtractedRequirement):
    if r.kind == "work_authorization":
        regions = [c.upper() for c in r.countries if c.upper() in COUNTRIES_OF_INTEREST | {"EU", "EEA", "EFTA", "US"}]
        return {"regions": regions} if regions else None
    if r.kind == "sponsorship":
        return None if r.available is None else {"available": r.available}
    if r.kind == "degree_level":
        levels = [lvl for lvl in r.levels if lvl in ("bachelor", "master", "phd", "any")]
        return {"levels": levels} if levels else None
    if r.kind == "graduation_year":
        years = [y for y in r.years if 2020 <= y <= 2040]
        return {"years": sorted(years)} if years else None
    if r.kind == "enrollment":
        return {"required": True}
    if r.kind == "duration_months":
        lo, hi = r.months_min, r.months_max or r.months_min
        return {"min": lo, "max": hi} if lo and hi and 1 <= lo <= hi <= 24 else None
    return {"summary": r.summary[:200]}


# --- verify (search-found postings) -------------------------------------------------------------------------------

@handler("verify")
def verify(ctx: Ctx, opp_id: str, payload: dict) -> None:
    """Check a posting found outside the employer's own sources against the posting page itself."""
    o = one(ctx.conn, "SELECT o.*, e.name employer, e.domain FROM opportunities o JOIN employers e ON "
            "e.id = o.employer_id WHERE o.id = ?", (opp_id,))
    if o is None or o["status"] != "pending":
        return
    url = payload.get("url") or o["canonical_url"]
    try:
        resp = ctx.http.get(url)
    except SourceFailure as f:
        if f.kind == "gone":
            lifecycle.transition(ctx.conn, ctx.now, opp_id, "closed", f"posting page {url} answers HTTP "
                                 f"{f.http_status}")
            return
        raise
    html = resp.text
    title_ok = quote_in(o["title"], html) or any(
        quote_in(o["title"], n.get("title", "")) for n in jsonld_postings(html, resp.url))
    ref = connectors.detect(resp.url)
    on_employer_domain = o["domain"] and registrable_domain(resp.url) == o["domain"]
    ctx.conn.execute("UPDATE opportunities SET last_verified_at = ? WHERE id = ?", (iso(ctx.now), opp_id))
    if not title_ok:
        return  # the page does not show this posting: leave it pending
    if ref is not None:
        sid, created = entities.register_source(ctx.conn, ctx.now, ref, employer_id=o["employer_id"],
                                                discovery_method="posting_url", association="probable",
                                                evidence=f"posting {url} of {o['employer']} is on this board")
        ctx.stats["sources_found"] += created
    if ref is not None or on_employer_domain:
        lifecycle.transition(ctx.conn, ctx.now, opp_id, "open", f"confirmed on the employer's own page {resp.url}")


# --- discover_sources ---------------------------------------------------------------------------------------------

_CAREER_LINK = re.compile(r"career|karriere|jobs?\b|emploi|carri[eè]re|empleo|trabaja|lavora|join\s+us|"
                          r"students?|graduates?|intern|stage|praktik|nachwuchs|einstieg|early\s+careers|talent|"
                          r"pr[aá]cticas?|practicante|becari|tirocin", re.I)
_EARLY = re.compile(r"student|graduate|intern|stage|praktik|nachwuchs|einstieg|early|campus|young|"
                    r"practicante|pr[aá]cticas|universit", re.I)


@handler("discover_sources")
def discover_sources(ctx: Ctx, employer_id: str, payload: dict) -> None:
    """Find an employer's recruitment sources: ATS boards named like it, and careers links on its own website."""
    e = one(ctx.conn, "SELECT * FROM employers WHERE id = ?", (employer_id,))
    if e is None or e["status"] != "active":
        return
    found = 0
    for ref, display in probe_boards(ctx, e["name"]):
        _, created = entities.register_source(ctx.conn, ctx.now, ref, employer_id=employer_id,
                                              discovery_method="ats_probe", priority=e["priority"],
                                              evidence=f"board name {display!r} matches {e['name']!r}")
        found += created
    if e["website"]:
        found += crawl_careers(ctx, employer_id, e["website"], e["priority"])
    ctx.stats["sources_found"] += found
    ctx.conn.execute("UPDATE coverage_gaps SET resolution = COALESCE(resolution, ?) WHERE employer_id = ? AND "
                     "resolved_at IS NULL", (f"source discovery found {found} new source(s)", employer_id))


def slug_candidates(name: str) -> list[str]:
    head = name.split(",")[0]
    words = re.findall(r"[a-z0-9]+", fold(head))
    core = name_tokens(head)
    out = ["".join(core), "-".join(core), "".join(words), "-".join(words)]
    if len(core) > 1 and len(core[0]) >= 4:
        out.append(core[0])
    return list(dict.fromkeys(s for s in out if s))[:5]


PROBE_KINDS = ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee")
_BOARD_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
              "ashby": "https://jobs.ashbyhq.com/{}", "smartrecruiters": "https://jobs.smartrecruiters.com/{}",
              "recruitee": "https://{}.recruitee.com"}


def probe_boards(ctx: Ctx, name: str) -> list[tuple[SourceRef, str]]:
    """ATS boards whose slug and display name match the firm. Boards without a usable name need a strong slug."""
    hits = []
    for slug in slug_candidates(name):
        for kind in PROBE_KINDS:
            ref = SourceRef(kind, _BOARD_URL[kind].format(slug), slug)
            try:
                display = connectors.get(kind).validate(ref, ctx.http)
            except SourceFailure:
                continue
            if not display:
                continue
            if display != slug and not same_firm(display, name):
                continue  # a board with another firm's name
            if display == slug and len(slug) < 6:
                continue  # short slugs collide across firms ("abc"): not enough evidence
            hits.append((ref, display))
        if hits:
            break
    return hits


def crawl_careers(ctx: Ctx, employer_id: str, website: str, priority: int = 2) -> int:
    """Homepage -> careers links (same registrable domain or a known ATS) -> one level deeper. Bounded."""
    domain = registrable_domain(website)
    budget = ctx.config.max_pages_per_candidate
    queue, visited, created = [website], set(), 0
    page_sources = 0
    while queue and budget > 0:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        budget -= 1
        try:
            resp = ctx.http.get(url)
        except SourceFailure:
            continue
        for link, label in links(resp.text, resp.url):
            ref = connectors.detect(link)
            if ref is not None:
                _, c = entities.register_source(ctx.conn, ctx.now, ref, employer_id=employer_id,
                                                discovery_method="homepage_link", priority=priority,
                                                evidence=f"linked from {resp.url} ({label[:60]})")
                created += c
                continue
            if registrable_domain(link) != domain or not _CAREER_LINK.search(f"{label} {link}"):
                continue
            if _EARLY.search(f"{label} {link}") and page_sources < 2:
                _, c = entities.register_source(
                    ctx.conn, ctx.now, SourceRef("page", link), employer_id=employer_id,
                    discovery_method="homepage_link", category="early_careers", priority=priority,
                    evidence=f"student/graduate link {label[:60]!r} on {resp.url}")
                created += c
                page_sources += c
            elif len(visited) + len(queue) < ctx.config.max_pages_per_candidate * 2:
                queue.append(link)
    if created == 0 and len(visited) > 1 and page_sources == 0:
        # careers pages exist but no board or student page: monitor the first careers page itself
        for url in list(visited)[1:2]:
            _, c = entities.register_source(ctx.conn, ctx.now, SourceRef("page", url), employer_id=employer_id,
                                            discovery_method="homepage_link", priority=priority,
                                            evidence=f"careers page reached from {website}")
            created += c
    return created


@handler("research")
def research(ctx: Ctx, employer_id: str, payload: dict) -> None:
    from tracker import prospects
    if not prospects.research(ctx, employer_id):
        raise Defer("AI budget or outage")
