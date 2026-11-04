import json

import httpx
import pytest
from pydantic import BaseModel

from conftest import FakeProvider, make_ctx
from tracker.ai import (MODEL, AIAccountError, AITransient, AIUnusable, GeminiProvider, quote_in)


class Out(BaseModel):
    answer: str


def gemini(replies):
    seen = []

    def handle(request):
        seen.append(request)
        r = replies.pop(0)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)
    return GeminiProvider("test-key", http=httpx.Client(transport=httpx.MockTransport(handle))), seen


def ok_body(text, finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}}


def test_gemini_sends_key_in_header_schema_and_minimal_thinking():
    p, seen = gemini([ok_body('{"answer": "x"}')])
    resp = p.generate(system="s", content="c", schema=Out.model_json_schema(), max_tokens=10)
    req = seen[0]
    assert "test-key" not in str(req.url) and req.headers["x-goog-api-key"] == "test-key"
    assert MODEL in str(req.url)
    body = json.loads(req.content)
    assert body["generationConfig"]["responseJsonSchema"]["properties"]["answer"]
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert (resp.input_tokens, resp.output_tokens) == (100, 25) and resp.text == '{"answer": "x"}'


def test_gemini_search_parses_grounding_sources():
    body = ok_body("Acme — Intern — Zurich")
    body["candidates"][0]["groundingMetadata"] = {"webSearchQueries": ["q"], "groundingChunks": [
        {"web": {"uri": "https://redirect.example/1", "title": "acme.com"}}]}
    p, seen = gemini([body])
    resp = p.generate(system="s", content="c", schema=None, max_tokens=10, search=True)
    assert json.loads(seen[0].content)["tools"] == [{"google_search": {}}]
    assert resp.sources == [("https://redirect.example/1", "acme.com")] and resp.search_calls == 1


@pytest.mark.parametrize("status,msg,exc", [(429, "slow down", AITransient), (503, "busy", AITransient),
                                            (401, "unauthorised", AIAccountError),
                                            (400, "API key not valid", AIAccountError),
                                            (400, "input too long", AIUnusable)])
def test_gemini_http_errors(status, msg, exc):
    p, _ = gemini([httpx.Response(status, json={"error": {"message": msg}})])
    with pytest.raises(exc):
        p.generate(system="s", content="c", schema=None, max_tokens=10)


def test_gemini_network_error_is_transient():
    def boom(request):
        raise httpx.ConnectError("down")
    p = GeminiProvider("k", http=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(AITransient):
        p.generate(system="s", content="c", schema=None, max_tokens=10)


def test_service_validates_caches_and_records_usage():
    prov = FakeProvider(default='{"answer": "42"}')
    ctx = make_ctx(provider=prov)
    assert ctx.ai.run("t", "1", "sys", "content", Out).answer == "42"
    assert ctx.ai.run("t", "1", "sys", "content", Out).answer == "42"   # cached
    assert len(prov.calls) == 1
    ctx.ai.flush()
    rows = [tuple(r) for r in ctx.conn.execute("SELECT task, outcome, input_tokens FROM ai_calls ORDER BY id")]
    assert rows == [("t", "ok", 1000), ("t", "cached", 0)]
    assert ctx.ai.run("t", "2", "sys", "content", Out) is not None   # a new prompt version is not a cache hit
    assert len(prov.calls) == 2


def test_invalid_output_is_billed_and_raises():
    ctx = make_ctx(provider=FakeProvider(default="not json"))
    with pytest.raises(AIUnusable):
        ctx.ai.run("t", "1", "sys", "c", Out)
    ctx.ai.flush()
    row = ctx.conn.execute("SELECT outcome, cost_usd FROM ai_calls").fetchone()
    assert row["outcome"] == "invalid" and row["cost_usd"] > 0


def test_transient_error_returns_none_and_is_retryable():
    ctx = make_ctx(provider=FakeProvider(default=AITransient("503")))
    assert ctx.ai.run("t", "1", "sys", "c", Out) is None
    assert ctx.ai.available


def test_account_error_blocks_the_rest_of_the_run():
    prov = FakeProvider(default=AIAccountError("401"))
    ctx = make_ctx(provider=prov)
    with pytest.raises(AIAccountError):
        ctx.ai.run("t", "1", "sys", "c", Out)
    assert not ctx.ai.available and ctx.ai.run("t", "1", "sys", "other", Out) is None
    assert len(prov.calls) == 1


def test_budget_stop_makes_no_call():
    from tracker.config import Config
    prov = FakeProvider(default='{"answer": "x"}')
    ctx = make_ctx(provider=prov, config=Config(ai_daily_requests=1))
    assert ctx.ai.run("t", "1", "sys", "a", Out) is not None
    assert ctx.ai.run("t", "1", "sys", "b", Out) is None  # second request today: over the daily limit
    assert len(prov.calls) == 1 and ctx.ai.budget.exhausted_reason == "daily_requests"


def test_no_key_means_no_calls():
    ctx = make_ctx(provider=None)
    assert not ctx.ai.available and ctx.ai.run("t", "1", "s", "c", Out) is None


def test_quote_in_ignores_case_and_punctuation_but_not_content():
    text = "Applications close on 15 December 2026. Fluent German is required."
    assert quote_in("applications close on 15 December, 2026", text)
    assert not quote_in("Applications close on 16 December 2026", text)
    assert not quote_in("", text) and not quote_in(None, text)
