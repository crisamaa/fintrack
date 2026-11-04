"""Market-wide discovery by web search (Channel B), behind a replaceable provider.

Providers (SEARCH_PROVIDER):
  none    default. No search is made; the Sources view says so.
  gemini  Grounding with Google Search through the Gemini API. On Gemini 3.x: 5,000 free grounded prompts a month,
          then $14 per 1,000 (checked 2026-11-04). Free-tier keys may not have access; failures are reported.
  brave   Brave Search API (needs BRAVE_API_KEY; check its current plan and terms before enabling).
Every search counts against SEARCH_DAILY_LIMIT (default 20/day).

The planner builds queries from facets (internship type x sector x country, in the local languages), runs
never-tried queries first, then the best-yielding ones, and always reserves a share of each day's searches for
the least-explored facets so the system does not only reinforce what it already finds.

Results feed the same pipeline as monitoring: an ATS board URL becomes a monitored source; a posting page with
schema.org JobPosting data becomes a pending opportunity (verified later against the employer's own site) and,
if its employer is already monitored, a coverage gap (Workflow C). Unknown hiring organisations become
discovery candidates.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from tracker import connectors, entities, ingest, prompts
from tracker.ai import AIAccountError
from tracker.connectors import SourceRef
from tracker.connectors.page import jsonld_postings, posting_from_jsonld
from tracker.context import Ctx
from tracker.db import dumps, iso, one, tx
from tracker.http import SourceFailure
from tracker.titles import looks_like_internship

log = logging.getLogger(__name__)
EXPLORE_SHARE = 0.25
MAX_RESULTS = 8


@dataclass
class Result:
    url: str
    title: str = ""
    snippet: str = ""


class SearchProvider(Protocol):
    name: str

    def search(self, ctx: Ctx, query: str) -> list[Result] | None: ...


class GeminiSearch:
    name = "gemini"

    def search(self, ctx, query):
        if ctx.ai is None:
            return None
        resp = ctx.ai.search(*prompts.SEARCH, prompts.SEARCH_SYSTEM, query)
        if resp is None:
            return None
        out = []
        for url, title in resp.sources[:MAX_RESULTS]:
            try:  # grounding links are redirects; resolve to the real page
                final = ctx.http.get(url).url
            except SourceFailure:
                continue
            out.append(Result(final, title))
        return out


class BraveSearch:
    name = "brave"
    API = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, ctx, query):
        if not ctx.ai.budget.can_search():
            return None
        try:
            data = ctx.http.get_json(self.API, params={"q": query, "count": MAX_RESULTS},
                                     headers={"X-Subscription-Token": self.api_key})
        except SourceFailure as e:
            log.warning("brave search failed: %s", e)
            return None
        finally:
            ctx.ai.pending.append((iso(ctx.now), "search", "brave", "brave", hashlib.sha1(query.encode()).hexdigest(),
                                   0, 0, 1, 0.0, "ok", None))
        return [Result(r["url"], r.get("title", ""), r.get("description", ""))
                for r in (data.get("web") or {}).get("results", [])[:MAX_RESULTS] if r.get("url")]


def provider_for(ctx: Ctx) -> SearchProvider | None:
    name = ctx.config.search_provider
    if name == "gemini" and ctx.ai and ctx.ai.provider:
        return GeminiSearch()
    if name == "brave" and ctx.config.brave_api_key:
        return BraveSearch(ctx.config.brave_api_key)
    return None


# --- query planning ---------------------------------------------------------------------------------------------
TERMS = {  # internship words by language
    "en": {"summer": "summer internship", "off_cycle": "off-cycle internship", "insight": "spring insight week",
           "intern": "internship", "working_student": "working student"},
    "de": {"intern": "Praktikum", "working_student": "Werkstudent"},
    "fr": {"intern": "stage", "summer": "stage d'été"},
    "it": {"intern": "stage tirocinio"},
    "es": {"intern": "prácticas", "summer": "prácticas de verano"},
}
SECTOR_TERMS = {"investment_banking": {"en": "investment banking", "de": "Investment Banking", "fr": "banque d'affaires",
                                       "es": "banca de inversión", "it": "investment banking"},
                "m_and_a": {"en": "M&A", "de": "M&A", "fr": "fusions acquisitions", "es": "M&A", "it": "M&A"},
                "private_equity": {"en": "private equity", "de": "Private Equity", "fr": "private equity",
                                   "es": "private equity", "it": "private equity"},
                "asset_management": {"en": "asset management", "de": "Asset Management", "fr": "gestion d'actifs",
                                     "es": "gestión de activos", "it": "asset management"},
                "wealth_management": {"en": "wealth management", "de": "Vermögensverwaltung",
                                      "fr": "gestion de fortune", "es": "banca privada", "it": "private banking"},
                "sales_trading": {"en": "sales and trading", "de": "Trading", "fr": "marchés financiers",
                                  "es": "mercados financieros", "it": "trading"},
                "venture_capital": {"en": "venture capital", "de": "Venture Capital", "fr": "capital-risque",
                                    "es": "venture capital", "it": "venture capital"},
                "risk": {"en": "financial risk", "de": "Risikomanagement", "fr": "risques financiers",
                         "es": "riesgos financieros", "it": "risk management"},
                "fintech": {"en": "fintech", "de": "Fintech", "fr": "fintech", "es": "fintech", "it": "fintech"}}
PLACES = {"CH": [("Zurich", "en"), ("Zürich", "de"), ("Genève", "fr"), ("Lugano", "it")],
          "PE": [("Lima", "es")], "GB": [("London", "en")], "DE": [("Frankfurt", "de")], "FR": [("Paris", "fr")],
          "LU": [("Luxembourg", "fr")], "NL": [("Amsterdam", "en")], "IT": [("Milano", "it")],
          "ES": [("Madrid", "es")], "IE": [("Dublin", "en")]}


def plan_queries(ctx: Ctx, provider: str) -> int:
    """Insert the facet grid of queries (idempotent)."""
    n = 0
    for (country, places), (sector, words) in itertools.product(PLACES.items(), SECTOR_TERMS.items()):
        for place, lang in places:
            for ttype, term in TERMS.get(lang, TERMS["en"]).items():
                query = f"{term} {words.get(lang, words['en'])} {place} {ctx.now.year + (1 if ctx.now.month >= 7 else 0)}"
                cur = ctx.conn.execute("INSERT OR IGNORE INTO search_queries (provider, query, facet) VALUES (?, ?, ?)",
                                       (provider, query, dumps({"country": country, "sector": sector, "type": ttype,
                                                                "lang": lang})))
                n += cur.rowcount
    return n


def pick(ctx: Ctx, provider: str, k: int) -> list:
    rows = ctx.conn.execute("SELECT * FROM search_queries WHERE provider = ? AND (next_run_at IS NULL OR "
                            "next_run_at <= ?)", (provider, iso(ctx.now))).fetchall()
    rng = random.Random(f"{ctx.today}:{provider}")
    never = [r for r in rows if r["runs"] == 0]
    tried = sorted((r for r in rows if r["runs"]), key=lambda r: -(r["new_opportunities"] + 2 * r["new_employers"])
                   / r["runs"])
    explore_n = max(1, int(k * EXPLORE_SHARE)) if k > 1 else 0
    rng.shuffle(never)
    chosen = never[:k - explore_n] + tried[:max(0, k - explore_n - len(never))]
    rest = [r for r in rows if r not in chosen]
    rest.sort(key=lambda r: r["runs"])  # least explored first, then random among them
    pool = rest[:max(explore_n * 4, 1)]
    rng.shuffle(pool)
    return (chosen + pool[:explore_n])[:k]


def run_search(ctx: Ctx) -> dict[str, int]:
    counts = {"queries": 0, "results": 0, "new_opportunities": 0, "new_candidates": 0, "new_sources": 0}
    prov = provider_for(ctx)
    if prov is None:
        return counts
    with tx(ctx.conn):
        plan_queries(ctx, prov.name)
        search_source = _search_source(ctx, prov.name)
    for q in pick(ctx, prov.name, ctx.config.search_daily_limit):
        if not ctx.time_left():
            break
        try:
            results = prov.search(ctx, q["query"])
        except AIAccountError:
            break
        finally:
            if ctx.ai:
                ctx.ai.flush()
        if results is None:
            break  # budget or provider trouble: stop for today
        counts["queries"] += 1
        with tx(ctx.conn):
            got = handle_results(ctx, search_source, results)
            ctx.conn.execute("UPDATE search_queries SET runs = runs + 1, results_total = results_total + ?, "
                             "new_opportunities = new_opportunities + ?, new_employers = new_employers + ?, "
                             "last_run_at = ?, next_run_at = ? WHERE id = ?",
                             (len(results), got["new_opportunities"], got["new_candidates"], iso(ctx.now),
                              iso(ctx.now + timedelta(days=3 if got["new_opportunities"] else 14)), q["id"]))
        for k2, v in got.items():
            counts[k2] += v
        counts["results"] += len(results)
    for k2, v in counts.items():
        ctx.stats[f"search_{k2}"] += v
    return counts


def _search_source(ctx: Ctx, provider: str) -> dict:
    ref = SourceRef("search", f"search://{provider}", provider)
    entities.register_source(ctx.conn, ctx.now, ref, employer_id=None, discovery_method="search",
                             association="unverified", category="aggregator", enabled=False,
                             evidence=f"results of {provider} web search")
    return dict(one(ctx.conn, "SELECT * FROM sources WHERE id = ?", (ref.id,)))


def handle_results(ctx: Ctx, search_source: dict, results: list[Result]) -> dict[str, int]:
    got = {"new_opportunities": 0, "new_candidates": 0, "new_sources": 0}
    for r in results:
        ref = connectors.detect(r.url)
        if ref is not None and ref.kind != "page":
            employer = _employer_for_board(ctx, ref)
            _, created = entities.register_source(
                ctx.conn, ctx.now, ref, employer_id=employer, discovery_method="search",
                association="probable" if employer else "unverified", evidence=f"search result {r.url}")
            got["new_sources"] += created
        try:
            resp = ctx.http.get(r.url)
        except SourceFailure:
            continue
        nodes = jsonld_postings(resp.text, resp.url)
        postings = [p for p in (posting_from_jsonld(n, resp.url) for n in nodes) if p and looks_like_internship(p.title)]
        for p in postings:
            before = one(ctx.conn, "SELECT COUNT(*) n FROM employer_candidates")["n"]
            out = ingest.apply_postings(ctx, search_source, [p], complete=False)
            got["new_opportunities"] += out.new_opportunities
            got["new_candidates"] += one(ctx.conn, "SELECT COUNT(*) n FROM employer_candidates")["n"] - before
    return got


def _employer_for_board(ctx: Ctx, ref: SourceRef) -> str | None:
    try:
        display = connectors.get(ref.kind).validate(ref, ctx.http)
    except SourceFailure:
        return None
    if not display or display == ref.board:
        return None
    found = entities.resolve(ctx.conn, display)
    if found is None:
        entities.add_candidate(ctx.conn, ctx.now, name=display, origin="search", evidence_url=ref.url,
                               evidence=f"{ref.kind} board found by search")
    return found
