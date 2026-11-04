import json

import httpx

from conftest import FIX, client, make_ctx
from helpers import employer, source
from tracker import search
from tracker.ai import AIResponse
from tracker.config import Config
from tracker.db import one

POSTING_URL = "https://www.examplepartners.ch/careers"


class SearchProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    def generate(self, *, system, content, schema, max_tokens, search=False):
        self.calls += 1
        return AIResponse(text="x", search_calls=1, sources=[(POSTING_URL, "examplepartners.ch")])


def ctx_with_search(limit=3):
    html = (FIX / "careers_jsonld.html").read_text()
    http = client(lambda r: httpx.Response(200, text=html) if str(r.url).startswith(POSTING_URL) else None)
    prov = SearchProvider()
    ctx = make_ctx(http=http, provider=prov, config=Config(search_provider="gemini", search_daily_limit=limit))
    return ctx, prov


def test_no_provider_means_no_search():
    ctx = make_ctx(config=Config(search_provider="none"))
    assert search.run_search(ctx)["queries"] == 0


def test_search_finds_posting_and_unknown_employer_becomes_candidate():
    ctx, prov = ctx_with_search(limit=2)
    out = search.run_search(ctx)
    assert out["queries"] == 2 and prov.calls == 2
    c = dict(one(ctx.conn, "SELECT * FROM employer_candidates"))
    assert c["name"] == "Example Partners SA" and c["website"] == "https://www.examplepartners.ch"
    assert one(ctx.conn, "SELECT COUNT(*) FROM search_queries WHERE runs = 1")[0] == 2


def test_search_find_for_known_employer_is_a_pending_opportunity_and_coverage_gap():
    ctx, _ = ctx_with_search(limit=1)
    employer(ctx, "ep", "Example Partners", website="https://www.examplepartners.ch")
    source(ctx, "ep", kind="greenhouse", board="ep")
    search.run_search(ctx)
    o = dict(one(ctx.conn, "SELECT * FROM opportunities"))
    assert o["employer_id"] == "ep" and o["status"] == "pending" and o["discovered_via"] == "search"
    assert one(ctx.conn, "SELECT cause FROM coverage_gaps")[0] == "missed_by_monitoring"


def test_daily_search_limit_stops_searching():
    ctx, prov = ctx_with_search(limit=50)
    ctx.ai.budget.daily_searches = 1
    search.run_search(ctx)
    assert prov.calls == 1


def test_planner_reserves_exploration_and_prefers_yield():
    ctx, _ = ctx_with_search()
    search.plan_queries(ctx, "fake")
    ctx.conn.execute("UPDATE search_queries SET runs = 1, new_opportunities = 5 WHERE id = 1")
    ctx.conn.execute("UPDATE search_queries SET runs = 1 WHERE id != 1")
    picked = search.pick(ctx, "fake", 8)
    assert picked[0]["id"] == 1 and len(picked) == 8
    facets = {json.loads(r["facet"])["country"] for r in picked}
    assert len(facets) > 1
