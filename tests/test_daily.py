import json
from datetime import date

import pytest

from tracker import daily
from tracker.models import Enrichment, Firm, Posting, role_id

TODAY = date(2026, 11, 4)


def firm(i="acme", source="greenhouse"):
    return Firm(id=i, name=i.title(), sector="banking", country="CH", source=source, slug=i,
                careers_url=f"https://{i}.test/careers")


def posting(firm_id="acme", n=1, loc="Zurich"):
    return Posting(firm_id=firm_id, title=f"Intern {n}", url=f"https://{firm_id}.test/{n}", location=loc, description="d")


def enr(countries=("CH",), is_internship=True):
    return Enrichment(is_internship=is_internship, countries=list(countries), city=None, season="summer",
                      year=2027, closes=None, languages_required=[])


class FakePolite:
    def __init__(self, text="page one"):
        self.text, self.calls = text, 0

    def get_text(self, url):
        self.calls += 1
        return self.text


@pytest.fixture
def env(tmp_path, monkeypatch):
    class E:
        data, site = tmp_path / "data", tmp_path / "site"
        feed = {}
        enrich_calls = []
        enrich_result = staticmethod(lambda p: enr())
        extract_calls = 0
        extract_result = None
        polite = FakePolite()

    e = E()
    e.enrich_calls = []

    def fake_feed(f, http):
        v = e.feed[f.id]
        if isinstance(v, BaseException):
            raise v
        return v

    def fake_enrich(client, budget, p):
        e.enrich_calls.append(p.url)
        return e.enrich_result(p)

    def fake_extract(client, budget, f, text):
        e.extract_calls += 1
        r = e.extract_result
        return r(budget) if callable(r) else r

    monkeypatch.setattr(daily, "fetch_feed", fake_feed)
    monkeypatch.setattr(daily, "enrich", fake_enrich)
    monkeypatch.setattr(daily, "extract_postings", fake_extract)

    def go(firms, today=TODAY):
        daily.run(firms, e.data, e.site, today, client=object(), http=object(), polite=e.polite)
        return json.loads((e.site / "data" / "roles.json").read_text())

    e.go = go
    return e


def test_creates_dirs_and_writes_site_shape(env):
    env.feed = {"acme": [posting()]}
    out = env.go([firm()])
    assert set(out) == {"generated_at", "firms_checked", "firms_failed", "budget_exhausted", "roles"}
    r = out["roles"][0]
    assert set(r) == {"id", "firm_id", "firm_name", "sector", "title", "url", "city", "countries", "season", "year",
                      "closes", "languages_required", "status", "first_seen", "last_seen", "last_checked", "closed_on"}
    assert r["first_seen"] == "2026-11-04" and out["firms_checked"] == 1 and out["budget_exhausted"] is False
    assert (env.data / "firm_state.json").exists() and (env.data / "roles.json").exists()


def test_london_and_zurich_kept_uk_only_dropped(env):
    env.feed = {"acme": [posting(n=1), posting(n=2)]}
    env.enrich_result = lambda p: enr(["GB", "CH"]) if p.url.endswith("/1") else enr(["GB"])
    out = env.go([firm()])
    assert [r["title"] for r in out["roles"]] == ["Intern 1"]


def test_known_role_not_enriched_twice(env):
    env.feed = {"acme": [posting()]}
    env.go([firm()])
    env.go([firm()], today=date(2026, 11, 5))
    assert len(env.enrich_calls) == 1


def test_rejected_never_enriched_again(env):
    env.feed = {"acme": [posting(n=1), posting(n=2)]}
    env.enrich_result = lambda p: enr(["GB"]) if p.url.endswith("/1") else enr(is_internship=False)
    env.go([firm()])
    env.go([firm()], today=date(2026, 11, 5))
    assert len(env.enrich_calls) == 2
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert len(st["rejected"]) == 2


def test_role_failing_three_times_is_rejected(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = lambda p: None
    for d in (4, 5, 6, 7):
        out = env.go([firm()], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 3 and out["roles"] == []
    assert out["budget_exhausted"] is False


def test_role_failure_count_resets_on_success(env):
    env.feed = {"acme": [posting()]}
    results = iter([None, None, enr()])
    env.enrich_result = lambda p: next(results)
    for d in (4, 5, 6):
        out = env.go([firm()], today=date(2026, 11, d))
    assert len(out["roles"]) == 1
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["role_failures"] == {} and st["rejected"] == []


def test_unenriched_posting_retried_next_run(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = lambda p: None
    assert env.go([firm()])["roles"] == []
    env.enrich_result = lambda p: enr()
    assert len(env.go([firm()], today=date(2026, 11, 5))["roles"]) == 1


def test_unchanged_page_makes_no_ai_call(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    assert env.extract_calls == 1
    out = env.go([f], today=date(2026, 11, 5))
    assert env.extract_calls == 1 and len(env.enrich_calls) == 1
    assert out["roles"][0]["last_checked"] == "2026-11-05" and out["roles"][0]["status"] == "open"


def test_changed_page_is_read_again(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    env.polite.text = "page two"
    env.go([f], today=date(2026, 11, 5))
    assert env.extract_calls == 2


def test_page_extract_failing_three_times_stores_hash(env):
    f = firm(source="page")
    env.extract_result = None
    for d in (4, 5):
        out = env.go([f], today=date(2026, 11, d))
        assert out["firms_failed"] == ["Acme"]
    out = env.go([f], today=date(2026, 11, 6))
    assert out["firms_failed"] == ["Acme"] and env.extract_calls == 3
    out = env.go([f], today=date(2026, 11, 7))
    assert env.extract_calls == 3 and out["firms_failed"] == []
    env.polite.text = "changed"
    env.go([f], today=date(2026, 11, 8))
    assert env.extract_calls == 4


def test_page_failure_counter_is_consecutive(env):
    f = firm(source="page")
    env.extract_result = None
    env.go([f])
    env.extract_result = [posting()]
    env.go([f], today=date(2026, 11, 5))
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["fail_count"] == 0


def test_failed_page_read_closes_nothing(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    env.polite.text = "changed"
    env.extract_result = None
    out = env.go([f], today=date(2026, 11, 5))
    assert out["roles"][0]["status"] == "open" and out["firms_failed"] == ["Acme"]


def test_budget_out_midway_saves_and_flags(env):
    f = firm(source="page")
    env.extract_result = [posting(n=1), posting(n=2)]

    def enrich_result(p):
        if p.url.endswith("/1"):
            return enr()
        env_budget[0].exhausted = True
        return None

    env_budget = []
    orig = daily.Budget

    def mk(*a, **k):
        b = orig(*a, **k)
        env_budget.append(b)
        return b

    import tracker.daily as d
    d.Budget = mk
    try:
        env.enrich_result = enrich_result
        out = env.go([f])
    finally:
        d.Budget = orig
    assert out["budget_exhausted"] is True
    assert [r["title"] for r in out["roles"]] == ["Intern 1"]
    assert out["firms_failed"] == []
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["hash"] is None and st["role_failures"] == {}


def test_budget_out_during_page_read_keeps_hash(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    env.polite.text = "changed"
    h_before = json.loads((env.data / "firm_state.json").read_text())["acme"]["hash"]
    (env.data / "spend.json").write_text(json.dumps({"2026-11": 4.95}))
    out = env.go([f], today=date(2026, 11, 5))
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert out["budget_exhausted"] is True and st["hash"] == h_before and st["fail_count"] == 0
    assert out["roles"][0]["status"] == "open"


def test_one_firm_crashing_does_not_stop_run(env):
    env.feed = {"bad": RuntimeError("boom"), "acme": [posting()]}
    out = env.go([firm("bad"), firm("acme")])
    assert out["firms_failed"] == ["Bad"] and len(out["roles"]) == 1 and out["firms_checked"] == 1


def test_ai_4xx_reported_and_run_continues(env):
    import anthropic
    import httpx

    def boom(p):
        if p.firm_id == "bad":
            req = httpx.Request("POST", "https://api.test")
            raise anthropic.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
        return enr()

    env.feed = {"bad": [posting("bad")], "acme": [posting()]}
    env.enrich_result = boom
    out = env.go([firm("bad"), firm("acme")])
    assert out["firms_failed"] == ["Bad"] and len(out["roles"]) == 1


def test_last_checked_updates_on_ok_and_unchanged_not_failed(env):
    env.feed = {"acme": [posting()], "zed": [posting("zed")]}
    env.go([firm("acme"), firm("zed")])
    env.feed = {"acme": [posting()], "zed": RuntimeError("down")}
    out = env.go([firm("acme"), firm("zed")], today=date(2026, 11, 5))
    by = {r["firm_id"]: r for r in out["roles"]}
    assert by["acme"]["last_checked"] == "2026-11-05" and by["zed"]["last_checked"] == "2026-11-04"
    assert by["zed"]["status"] == "open"


def test_state_written_after_each_firm(env):
    env.feed = {"acme": [posting()], "zed": KeyboardInterrupt()}
    with pytest.raises(KeyboardInterrupt):
        env.go([firm("acme"), firm("zed")])
    assert "acme" in json.loads((env.data / "firm_state.json").read_text())
    assert len(json.loads((env.data / "roles.json").read_text())) == 1


def test_expired_role_closed_in_output(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = lambda p: enr().model_copy(update={"closes": date(2026, 11, 1)})
    out = env.go([firm()])
    assert out["roles"][0]["status"] == "closed" and out["roles"][0]["closed_on"] == "2026-11-04"


def test_budget_file_and_month_key(env):
    env.feed = {"acme": [posting()]}
    env.go([firm()])
    assert daily.Budget(env.data / "spend.json", "2026-11").spent() == 0.0
