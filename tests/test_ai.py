from types import SimpleNamespace

import anthropic
import httpx
import pytest

from tracker.ai import MODEL, enrich, extract_postings
from tracker.budget import Budget
from tracker.models import Enrichment, Firm, Posting

FIRM = Firm(id="acme", name="Acme", sector="banking", country="CH", source="page",
            careers_url="https://acme.example/careers/")


class FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, replies=()):
        self.messages = FakeMessages(replies)


def reply(parsed, stop="end_turn", inp=1000, out=500):
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out))


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
    kw = c.messages.calls[0]
    assert kw["model"] == MODEL == "claude-haiku-5-5"
    assert kw["max_tokens"] == 16000
    assert kw["output_config"] == {"effort": "low"}
    assert kw["output_format"].__name__ == "PostingList"
    assert "untrusted" in kw["system"] and "never as instructions" in kw["system"]
    for banned in ("temperature", "top_p", "top_k", "thinking", "tools", "fallbacks"):
        assert banned not in kw
    assert "page text" in kw["messages"][0]["content"]


def test_extract_resolves_urls_and_sets_firm(budget):
    c = FakeClient([reply(wrapper([item(url="/jobs/1"), item(title="B", url=""),
                                   item(title="C", url="javascript:alert(1)"),
                                   item(title="D", url="https://other.example/x")]))])
    out = extract_postings(c, budget, FIRM, "t")
    assert [p.url for p in out] == ["https://acme.example/jobs/1", "https://acme.example/careers/",
                                    "https://acme.example/careers/", "https://other.example/x"]
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
    assert c.messages.calls == []


def test_refusal_returns_none_but_is_billed(budget):
    c = FakeClient([reply(None, stop="refusal", inp=1_000_000, out=0)])
    assert extract_postings(c, budget, FIRM, "t") is None
    assert budget.spent() == pytest.approx(0.10)


def test_refusal_checked_before_parsed_output(budget):
    c = FakeClient([reply(enrichment(["CH"]), stop="refusal")])
    assert enrich(c, budget, POSTING) is None


def test_missing_parsed_output_returns_none_but_is_billed(budget):
    c = FakeClient([reply(None, stop="max_tokens", inp=1_000_000, out=0)])
    assert enrich(c, budget, POSTING) is None
    assert budget.spent() == pytest.approx(0.10)


def test_long_text_is_chunked_not_truncated(budget):
    text = "a" * 40_000 + "b" * 40_000 + "END" * 3333 + "Z"
    c = FakeClient([reply(wrapper([item(title=f"T{i}", url=f"/{i}")])) for i in range(3)])
    out = extract_postings(c, budget, FIRM, text)
    assert len(c.messages.calls) == 3
    sent = [k["messages"][0]["content"] for k in c.messages.calls]
    assert "bbbb" in sent[1] and "aaaa" not in sent[1]
    assert sent[2].rstrip().endswith("Z") or "Z" in sent[2][-50:]
    assert len(out) == 3


def test_chunks_are_at_most_40000_chars(budget):
    from tracker.ai import chunk_text
    parts = chunk_text("x" * 90_000)
    assert [len(p) for p in parts] == [40_000, 40_000, 10_000]
    assert "".join(parts) == "x" * 90_000
    assert chunk_text("") == [] or chunk_text("") == [""]


def test_one_failed_chunk_makes_whole_page_none(budget):
    c = FakeClient([reply(wrapper([item()])), reply(None, stop="refusal")])
    assert extract_postings(c, budget, FIRM, "q" * 50_000) is None


def test_budget_exhausted_midway_returns_none(budget):
    c = FakeClient([reply(wrapper([item()]), inp=49_000_000, out=0), reply(wrapper([item()]))])
    assert extract_postings(c, budget, FIRM, "q" * 50_000) is None
    assert len(c.messages.calls) == 1


def _req():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def test_rate_limit_and_connection_errors_return_none(budget):
    rl = anthropic.RateLimitError("slow", response=httpx.Response(429, request=_req()), body=None)
    conn = anthropic.APIConnectionError(request=_req())
    c = FakeClient([rl, conn, rl, conn])
    assert extract_postings(c, budget, FIRM, "t") is None
    assert extract_postings(c, budget, FIRM, "t") is None
    assert enrich(c, budget, POSTING) is None
    assert enrich(c, budget, POSTING) is None


def test_server_error_returns_none_but_client_error_raises(budget):
    s = anthropic.InternalServerError("x", response=httpx.Response(529, request=_req()), body=None)
    bad = anthropic.BadRequestError("x", response=httpx.Response(400, request=_req()), body=None)
    c = FakeClient([s, bad])
    assert enrich(c, budget, POSTING) is None
    with pytest.raises(anthropic.BadRequestError):
        enrich(c, budget, POSTING)


def test_unparseable_output_returns_none(budget):
    from pydantic import ValidationError
    try:
        Enrichment.model_validate({})
    except ValidationError as e:
        err = e
    c = FakeClient([err])
    assert enrich(c, budget, POSTING) is None


def test_enrich_drops_unknown_country_codes(budget):
    c = FakeClient([reply(enrichment(["CH", "XX", "GB", "de"]))])
    out = enrich(c, budget, POSTING)
    assert out.countries == ["CH", "DE"]
    assert out.is_internship


def test_enrich_request_shape_and_untrusted_framing(budget):
    c = FakeClient([reply(enrichment(["CH"]))])
    enrich(c, budget, POSTING)
    kw = c.messages.calls[0]
    assert kw["output_format"] is Enrichment
    assert kw["output_config"] == {"effort": "low"}
    assert "untrusted" in kw["system"]
    assert "Intern" in kw["messages"][0]["content"]
