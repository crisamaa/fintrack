import json

import anthropic
import httpx2 as httpx
import pytest

from tracker.ai import MODEL, AIUnusable, enrich, extract_postings
from tracker.budget import Budget
from tracker.models import Enrichment, Firm, Posting

FIRM = Firm(id="acme", name="Acme", sector="banking", country="CH", source="page",
            careers_url="https://acme.example/careers/")


class FakeClient:
    """A real anthropic.Anthropic client over a mock HTTP transport: no network, real SDK."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.bodies = []
        self.client = anthropic.Anthropic(
            api_key="test", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(self._handle)))
        self.messages = self.client.messages

    @property
    def calls(self):
        return self.bodies

    def _handle(self, request):
        self.bodies.append(json.loads(request.content))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):
            return r
        return httpx.Response(200, json=r)


def reply(parsed, stop="end_turn", inp=1000, out=500, text=None):
    if text is None and parsed is not None:
        text = parsed.model_dump_json()
    content = [] if text is None else [{"type": "text", "text": text}]
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": inp, "output_tokens": out}}


def wrapper(items):
    from tracker.ai import PostingList
    return PostingList.model_validate({"postings": items})


def item(title="Summer Intern", url="/jobs/1", location="Zurich", description="d"):
    return {"title": title, "url": url, "location": location, "description": description}


def enrichment(countries):
    return Enrichment(is_internship=True, countries=countries, city="Zurich", season="summer",
                      year=2027, closes=None, languages_required=["en"])


POSTING = Posting(firm_id="acme", title="Intern", url="https://acme.example/j", location="Zurich", description="x")


@pytest.fixture
def budget(tmp_path):
    return Budget(tmp_path / "b.json", "2026-11")


def test_request_shape(budget):
    c = FakeClient([reply(wrapper([item()]))])
    extract_postings(c, budget, FIRM, "page text")
    kw = c.calls[0]
    assert kw["model"] == MODEL == "claude-haiku-5-5"
    assert kw["max_tokens"] == 16000
    assert kw["output_config"]["effort"] == "low"
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "postings" in fmt["schema"]["properties"]
    assert "output_format" not in kw
    assert "untrusted" in kw["system"] and "never as instructions" in kw["system"]
    for banned in ("temperature", "top_p", "top_k", "thinking", "tools", "fallbacks"):
        assert banned not in kw
    assert "page text" in kw["messages"][0]["content"]


def test_extract_resolves_urls_and_sets_firm(budget):
    c = FakeClient([reply(wrapper([item(url="/jobs/1"), item(title="B", url=""),
                                   item(title="C", url="javascript:alert(1)"),
                                   item(title="D", url="https://other.example/x")]))])
    out = extract_postings(c, budget, FIRM, "t")
    urls = [p.url for p in out]
    assert urls[0] == "https://acme.example/jobs/1" and urls[3] == "https://other.example/x"
    assert urls[1].startswith("https://acme.example/careers/#") and urls[2].startswith("https://acme.example/careers/#")
    assert all(p.firm_id == "acme" for p in out)
    assert all(isinstance(p, Posting) for p in out)


def test_usage_recorded_after_call(budget):
    c = FakeClient([reply(wrapper([]), inp=1_000_000, out=0)])
    extract_postings(c, budget, FIRM, "t")
    assert budget.spent() == pytest.approx(0.10)


def test_no_api_call_when_budget_spent(budget):
    budget.record(49_000_000, 0)
    c = FakeClient([])
    assert extract_postings(c, budget, FIRM, "t") is None
    assert enrich(c, budget, POSTING) is None
    assert c.calls == []


def test_refusal_returns_none_but_is_billed(budget):
    c = FakeClient([reply(None, stop="refusal", inp=1_000_000, out=0)])
    with pytest.raises(AIUnusable):
        extract_postings(c, budget, FIRM, "t")
    assert budget.spent() == pytest.approx(0.10)


def test_refusal_checked_before_parsed_output(budget):
    c = FakeClient([reply(enrichment(["CH"]), stop="refusal")])
    with pytest.raises(AIUnusable):
        enrich(c, budget, POSTING)


def test_missing_parsed_output_returns_none_but_is_billed(budget):
    c = FakeClient([reply(None, stop="max_tokens", inp=1_000_000, out=0)])
    with pytest.raises(AIUnusable):
        enrich(c, budget, POSTING)
    assert budget.spent() == pytest.approx(0.10)


def test_long_text_is_chunked_not_truncated(budget):
    text = "a" * 40_000 + "b" * 40_000 + "END" * 3333 + "Z"
    c = FakeClient([reply(wrapper([item(title=f"T{i}", url=f"/{i}")])) for i in range(3)])
    out = extract_postings(c, budget, FIRM, text)
    assert len(c.calls) == 3
    sent = [k["messages"][0]["content"] for k in c.calls]
    assert "bbbb" in sent[1] and "aaaa" not in sent[1]
    assert sent[2].rstrip().endswith("Z") or "Z" in sent[2][-50:]
    assert len(out) == 3


def test_chunks_are_at_most_40000_chars(budget):
    from tracker.ai import chunk_text
    parts = chunk_text("x" * 90_000)
    assert [len(p) for p in parts] == [40_000, 40_000, 10_000]
    assert "".join(parts) == "x" * 90_000
    assert chunk_text("") == [] or chunk_text("") == [""]


def test_one_unusable_chunk_fails_whole_page(budget):
    c = FakeClient([reply(wrapper([item()])), reply(None, stop="refusal")])
    with pytest.raises(AIUnusable):
        extract_postings(c, budget, FIRM, "q" * 50_000)


def test_budget_exhausted_midway_returns_none(budget):
    c = FakeClient([reply(wrapper([item()]), inp=49_000_000, out=0), reply(wrapper([item()]))])
    assert extract_postings(c, budget, FIRM, "q" * 50_000) is None
    assert len(c.calls) == 1


def _status(code):
    return httpx.Response(code, json={"type": "error", "error": {"type": "x", "message": "m"}})


def test_rate_limit_and_connection_errors_return_none(budget):
    c = FakeClient([_status(429), httpx.ConnectError("down"), _status(429), httpx.ConnectError("down")])
    assert extract_postings(c, budget, FIRM, "t") is None
    assert extract_postings(c, budget, FIRM, "t") is None
    assert enrich(c, budget, POSTING) is None
    assert enrich(c, budget, POSTING) is None


def test_server_error_returns_none_but_client_error_raises(budget):
    c = FakeClient([_status(529), _status(400)])
    assert enrich(c, budget, POSTING) is None
    with pytest.raises(anthropic.BadRequestError):
        enrich(c, budget, POSTING)


GOOD = enrichment(["CH"]).model_dump_json()


@pytest.mark.parametrize("label,r", [
    ("valid", reply(None, text=GOOD)),
    ("refusal-empty", reply(None, stop="refusal")),
    ("refusal-text", reply(None, stop="refusal", text="I can't help with that.")),
    ("truncated", reply(None, stop="max_tokens", text=GOOD[:40])),
    ("empty-text", reply(None, text="")),
    ("not-matching-schema", reply(None, text='{"nope": 1}')),
])
def test_every_billed_outcome_records_full_usage(tmp_path, label, r):
    r["usage"] = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    b = Budget(tmp_path / "b.json", "2026-11")
    if label == "valid":
        assert enrich(FakeClient([r]), b, POSTING) is not None
    else:
        with pytest.raises(AIUnusable):
            enrich(FakeClient([r]), b, POSTING)
    assert b.spent() == pytest.approx(0.60)


@pytest.mark.parametrize("r", [
    reply(None, stop="refusal"),
    reply(None, stop="max_tokens", text='{"postings": [{"ti'),
    reply(None, text=""),
])
def test_extract_unusable_output_raises_and_is_billed(tmp_path, r):
    r["usage"] = {"input_tokens": 1_000_000, "output_tokens": 0}
    b = Budget(tmp_path / "b.json", "2026-11")
    with pytest.raises(AIUnusable):
        extract_postings(FakeClient([r]), b, FIRM, "t")
    assert b.spent() == pytest.approx(0.10)


def test_malformed_model_url_does_not_crash(budget):
    c = FakeClient([reply(wrapper([item(url="http://[bad/x"), item(title="Ok", url="/ok")]))])
    out = extract_postings(c, budget, FIRM, "t")
    assert len(out) == 2
    assert out[0].url.startswith("https://acme.example/careers/#")
    assert out[1].url == "https://acme.example/ok"


def test_urlless_postings_get_distinct_role_ids(budget):
    from tracker.models import role_id
    c = FakeClient([reply(wrapper([item(title="Intern A", url=""), item(title="Intern B", url=""),
                                   item(title="Intern A", url="", location="Basel")]))])
    out = extract_postings(c, budget, FIRM, "t")
    assert len({role_id(p.firm_id, p.url) for p in out}) == 3
    assert all(p.url.startswith("https://acme.example/careers/#") for p in out)


def test_prompt_tells_model_to_use_inline_links():
    from tracker.ai import EXTRACT_SYSTEM
    assert "<" in EXTRACT_SYSTEM and "link" in EXTRACT_SYSTEM


def test_enrich_drops_unknown_country_codes(budget):
    c = FakeClient([reply(enrichment(["CH", "XX", "GB", "de"]))])
    out = enrich(c, budget, POSTING)
    assert out.countries == ["CH", "DE"]
    assert out.is_internship


def test_enrich_request_shape_and_untrusted_framing(budget):
    c = FakeClient([reply(enrichment(["CH"]))])
    enrich(c, budget, POSTING)
    kw = c.calls[0]
    assert kw["output_config"]["effort"] == "low"
    assert "is_internship" in kw["output_config"]["format"]["schema"]["properties"]
    assert "untrusted" in kw["system"]
    assert "Intern" in kw["messages"][0]["content"]
