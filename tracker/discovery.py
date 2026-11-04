"""Autonomous employer discovery (Workflow A).

Candidates come from independent channels: financial directories (FINMA's list of authorised banks and securities
firms), hiring organisations named in postings found by search or on aggregators, coverage audits, and the seed
file firms/candidates.yaml. Each run verifies a bounded batch:

  1. normalise and check the name against known employers (duplicate -> linked, not re-added)
  2. find the official website (given by the channel, or proposed by grounded search) and verify identity: the
     page must name the firm
  3. decide relevance: directory category (FINMA = banking) or AI classification of the firm's own homepage,
     whose supporting quote must occur on the page
  4. register the employer with its evidence, then discover its recruitment sources (ATS probes, careers links)
A candidate with no website and no matching job board is retried monthly, at most MAX_ATTEMPTS times.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import timedelta
from urllib.parse import urljoin, urlsplit

import pypdf
from bs4 import BeautifulSoup

from tracker import entities, notify, prompts, work
from tracker.ai import AIAccountError, AIUnusable, quote_in
from tracker.context import Ctx
from tracker.db import get_meta, iso, parse_iso, set_meta, tx
from tracker.http import SourceFailure
from tracker.names import name_tokens
from tracker.taxonomy import SECTORS

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
RETRY = timedelta(days=30)
DIRECTORY_EVERY = timedelta(days=28)


# --- FINMA -------------------------------------------------------------------------------------------------------
FINMA_PAGE = "https://www.finma.ch/en/finma-public/authorised-institutions-individuals-and-products/"
_CATEGORY_ROW_END = re.compile(r"[1-5]")
_FOOTER = re.compile(r"\d+/\d+|\d{2}\.\d{2}\.\d{4} /.*")


def find_finma_pdf_url(page_html: str, base_url: str) -> str:
    soup = BeautifulSoup(page_html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text().split()).lower()
        if ("banks and securities firms" in text and "authorised" in text and "raiffeisen" not in text
                and urlsplit(a["href"]).path.lower().endswith(".pdf")):
            return urljoin(base_url, a["href"])
    raise SourceFailure("format_changed", "FINMA page: no PDF link for the authorised banks and securities firms list")


def parse_finma_names(pdf_bytes: bytes) -> list[str]:
    """Firm names from the list: name lines, city, category, then marker lines ending in a lone licensing digit."""
    if not pdf_bytes.startswith(b"%PDF"):
        raise SourceFailure("invalid", "FINMA list is not a PDF")
    try:
        pages = [p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages]
    except Exception as e:  # pypdf raises many types on bad files
        raise SourceFailure("invalid", f"FINMA list: cannot read PDF ({e})") from e
    names: list[str] = []
    buf: list[str] = []
    for page in pages:
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        if "Category" in lines:
            lines = lines[lines.index("Category") + 1:]
        for ln in lines:
            if _FOOTER.fullmatch(ln):
                continue
            if _CATEGORY_ROW_END.fullmatch(ln):
                entry = [x for x in buf if x != "X"]
                buf = []
                if len(entry) >= 3:
                    name = " ".join(" ".join(entry[:-2]).split(",")[0].split())
                    if name and name not in names:
                        names.append(name)
            else:
                buf.append(ln)
    if not names:
        raise SourceFailure("format_changed", "FINMA list: no firm names found in the PDF")
    return names


def finma_directory(ctx: Ctx) -> int:
    page = ctx.http.get(FINMA_PAGE)
    pdf = ctx.http.get(find_finma_pdf_url(page.text, page.url))
    n = 0
    for name in parse_finma_names(pdf.content):
        n += bool(entities.add_candidate(ctx.conn, ctx.now, name=name, origin="finma", country="CH",
                                         sector_hint="banking", evidence_url=FINMA_PAGE,
                                         evidence="FINMA list of authorised banks and securities firms"))
    return n


# Directories are pluggable: name -> function(ctx) returning the number of new candidates.
DIRECTORIES = {"finma": finma_directory}


def run_directories(ctx: Ctx) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    for name, fn in DIRECTORIES.items():
        last = parse_iso(get_meta(ctx.conn, f"directory:{name}:last_success"))
        if last and ctx.now - last < DIRECTORY_EVERY:
            continue
        try:
            with tx(ctx.conn):
                out[name] = fn(ctx)
                set_meta(ctx.conn, f"directory:{name}:last_success", iso(ctx.now))
        except SourceFailure as e:
            log.warning("directory %s failed: %s", name, e)
            out[name] = f"failed: {e}"
            set_meta(ctx.conn, f"directory:{name}:last_error", f"{iso(ctx.now)} {e}")
    return out


# --- candidates --------------------------------------------------------------------------------------------------

def _names_page(name: str, text: str) -> bool:
    tokens = [t for t in name_tokens(name) if len(t) >= 3]
    folded = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(tokens) and all(t in folded for t in tokens[:3])


def process_candidates(ctx: Ctx, limit: int | None = None) -> dict[str, int]:
    counts = {"accepted": 0, "duplicate": 0, "rejected": 0, "unresolved": 0, "deferred": 0}
    rows = ctx.conn.execute(
        "SELECT * FROM employer_candidates WHERE status IN ('new', 'unresolved') AND attempts < ? AND "
        "(next_attempt_at IS NULL OR next_attempt_at <= ?) ORDER BY status = 'unresolved', created_at, id LIMIT ?",
        (MAX_ATTEMPTS, iso(ctx.now), limit or ctx.config.discovery_candidates_per_run)).fetchall()
    for c in rows:
        if not ctx.time_left():
            break
        try:
            with tx(ctx.conn):
                outcome = _process(ctx, dict(c))
        except AIAccountError:
            outcome = "deferred"
        finally:
            if ctx.ai is not None:
                ctx.ai.flush()
        counts[outcome] += 1
    for k, v in counts.items():
        ctx.stats[f"candidates_{k}"] += v
    return counts


def _set(ctx: Ctx, cid: int, status: str, note: str, employer_id: str | None = None, retry: bool = False) -> None:
    ctx.conn.execute("UPDATE employer_candidates SET status = ?, note = ?, employer_id = COALESCE(?, employer_id), "
                     "attempts = attempts + 1, next_attempt_at = ?, updated_at = ? WHERE id = ?",
                     (status, note[:500], employer_id, iso(ctx.now + RETRY) if retry else None, iso(ctx.now), cid))


def _process(ctx: Ctx, c: dict) -> str:
    known = entities.resolve(ctx.conn, c["name"], website=c["website"], country=c["country"])
    if known:
        entities.add_alias(ctx.conn, known, c["name"], c["origin"])
        _set(ctx, c["id"], "duplicate", f"same firm as {known}", known)
        return "duplicate"
    website, homepage = c["website"], None
    if website:
        try:
            resp, homepage = ctx.http.get_text(website)
            website = f"{urlsplit(resp.url).scheme}://{urlsplit(resp.url).netloc}"
        except SourceFailure as e:
            _set(ctx, c["id"], "unresolved", f"website unreachable: {e}", retry=True)
            return "unresolved"
        if not _names_page(c["name"], homepage):
            _set(ctx, c["id"], "unresolved", f"{website} does not name {c['name']!r}: identity not verified",
                 retry=True)
            return "unresolved"
    boards = work.probe_boards(ctx, c["name"])
    if not website and not boards:
        _set(ctx, c["id"], "unresolved", "no official website known and no job board with this name", retry=True)
        return "unresolved"
    sectors = [c["sector_hint"]] if c["sector_hint"] in SECTORS else []
    countries = [c["country"]] if c["country"] else []
    size, method, evidence = None, "directory", c["evidence"] or c["origin"]
    if not sectors:
        if homepage is None:
            _set(ctx, c["id"], "unresolved", "relevance unknown: no website to classify", retry=True)
            return "unresolved"
        if ctx.ai is None or not ctx.ai.available:
            ctx.conn.execute("UPDATE employer_candidates SET note = 'waiting for AI classification', "
                             "next_attempt_at = ? WHERE id = ?", (iso(ctx.now + timedelta(days=1)), c["id"]))
            return "deferred"
        try:
            cls = ctx.ai.run(*prompts.CLASSIFY_EMPLOYER, prompts.CLASSIFY_EMPLOYER_SYSTEM,
                             f"Website of {c['name']} ({website}).\n\n<page_text>\n{homepage[:20000]}\n</page_text>",
                             prompts.EmployerClassification)
        except AIUnusable as e:
            _set(ctx, c["id"], "unresolved", f"classification failed: {e}", retry=True)
            return "unresolved"
        if cls is None:
            ctx.conn.execute("UPDATE employer_candidates SET note = 'waiting for AI budget', next_attempt_at = ? "
                             "WHERE id = ?", (iso(ctx.now + timedelta(days=1)), c["id"]))
            return "deferred"
        if not quote_in(cls.quote, homepage):
            _set(ctx, c["id"], "unresolved", "classification quote not on the page: not trusted", retry=True)
            return "unresolved"
        if not cls.is_finance_employer:
            _set(ctx, c["id"], "rejected", f"not a finance employer: {cls.quote[:200]}")
            return "rejected"
        sectors = [s for s in cls.sectors if s in SECTORS] or ["other_finance"]
        countries = list(dict.fromkeys(countries + [x.upper() for x in cls.countries if len(x) == 2]))
        size, method, evidence = cls.size_hint, "ai", f"{website}: “{cls.quote[:200]}”"
    eid = entities.register_employer(ctx.conn, ctx.now, name=c["name"], discovered_via=c["origin"].split(":")[0],
                                     website=website, country=countries[0] if countries else None, sectors=sectors,
                                     sector_method=method, evidence=evidence, size_hint=size)
    parent = entities.parent_candidate(ctx.conn, c["name"], countries[0] if countries else None)
    if parent and parent != eid:
        ctx.conn.execute("UPDATE employers SET parent_id = ? WHERE id = ?", (parent, eid))
    for country in countries[1:]:
        ctx.conn.execute("INSERT OR IGNORE INTO employer_locations VALUES (?, ?, '', ?)", (eid, country, method))
    for ref, display in boards:
        entities.register_source(ctx.conn, ctx.now, ref, employer_id=eid, discovery_method="ats_probe",
                                 evidence=f"board name {display!r} matches {c['name']!r}")
    if website:
        work.crawl_careers(ctx, eid, website)
    _relink_postings(ctx, eid)
    _set(ctx, c["id"], "accepted", f"registered as {eid}", eid)
    notify.new_employer(ctx, eid, c["name"], f"found via {c['origin']}; {evidence[:200]}")
    return "accepted"


def _relink_postings(ctx: Ctx, employer_id: str) -> None:
    """Postings seen before this employer existed (search, aggregators) are now linked to it."""
    from tracker import ingest
    from tracker.connectors import RawPosting
    from tracker.db import loads
    rows = ctx.conn.execute("SELECT sp.*, s.id sid FROM source_postings sp JOIN sources s ON s.id = sp.source_id "
                            "WHERE sp.opportunity_id IS NULL").fetchall()
    for r in rows:
        st = loads(r["structured"], {})
        if entities.resolve(ctx.conn, st.get("hiring_organization"), website=st.get("hiring_organization_url")) \
                != employer_id:
            continue
        src = dict(ctx.conn.execute("SELECT * FROM sources WHERE id = ?", (r["sid"],)).fetchone())
        ingest.apply_postings(ctx, src, [RawPosting(external_id=r["external_id"], title=r["title"], url=r["url"],
                                                     location=r["location_text"], description=r["description"],
                                                     published_at=r["published_at"], structured=st,
                                                     extraction=r["extraction"])], complete=False)
