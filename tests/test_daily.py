import json
from datetime import date

import anthropic
import httpx
import pytest

from tracker import daily
from tracker.ai import AIUnusable
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


def _raise_budget(exc):
    def f(budget):
        raise exc
    return f


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

    e.exit_failed = False

    def go(firms, today=TODAY):
        try:
            daily.run(firms, e.data, e.site, today, client=object(), http=object(), polite=e.polite)
        except daily.RunFailed:
            e.exit_failed = True
        return json.loads((e.site / "data" / "roles.json").read_text())

    e.go = go
    return e


def test_creates_dirs_and_writes_site_shape(env):
    env.feed = {"acme": [posting()]}
    out = env.go([firm()])
    assert set(out) == {"generated_at", "firms_checked", "firms_failed", "firms_unread", "firms_not_covered",
                       "budget_exhausted", "roles"}
    r = out["roles"][0]
    assert set(r) == {"id", "firm_id", "firm_name", "sector", "title", "url", "city", "countries", "season", "year",
                      "closes", "languages_required", "status", "first_seen", "last_seen", "last_checked", "closed_on"}
    assert r["first_seen"] == "2026-11-04" and out["firms_checked"] == 1 and out["budget_exhausted"] is False
    assert out["firms_unread"] == 0 and not env.exit_failed
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


def _raise(exc):
    def f(p):
        raise exc
    return f


def test_role_failing_three_times_is_given_up_not_rejected(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6, 7):
        out = env.go([firm()], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 3 and out["roles"] == []
    assert out["firms_failed"] == ["Acme"]  # a firm holding given-up ids stays listed
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["rejected"] == [] and len(st["given_up"]) == 1


def test_given_up_role_retried_after_30_days(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6):
        env.go([firm()], today=date(2026, 11, d))
    env.enrich_result = lambda p: enr()
    env.go([firm()], today=date(2026, 12, 5))  # 29 days after give-up on 11-06
    assert len(env.enrich_calls) == 3
    out = env.go([firm()], today=date(2026, 12, 6))
    assert len(env.enrich_calls) == 4 and len(out["roles"]) == 1 and out["firms_failed"] == []


def test_given_up_role_retried_when_page_changes(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6):
        env.polite.text = f"v{d}"
        env.go([f], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 3
    env.polite.text = "v-new"
    env.enrich_result = lambda p: enr()
    out = env.go([f], today=date(2026, 11, 7))
    assert len(out["roles"]) == 1


def test_transient_enrich_failure_never_rejects(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = lambda p: None  # outage / 429: nothing billed
    for d in (4, 5, 6, 7, 8):
        env.go([firm()], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 5
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["rejected"] == [] and st["given_up"] == {} and st["role_failures"] == {}
    env.enrich_result = lambda p: enr()
    assert len(env.go([firm()], today=date(2026, 11, 9))["roles"]) == 1


def test_role_failure_count_resets_on_success(env):
    env.feed = {"acme": [posting()]}
    results = iter([AIUnusable("x"), AIUnusable("x"), enr()])

    def res(p):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r

    env.enrich_result = res
    for d in (4, 5, 6):
        out = env.go([firm()], today=date(2026, 11, d))
    assert len(out["roles"]) == 1
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["role_failures"] == {} and st["given_up"] == {}


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


def test_page_extract_failing_three_times_gives_up_but_stays_listed(env):
    f = firm(source="page")
    env.extract_result = _raise_budget(AIUnusable("bad"))
    for d in (4, 5, 6):
        out = env.go([f], today=date(2026, 11, d))
        assert out["firms_failed"] == ["Acme"]
    assert env.extract_calls == 3
    out = env.go([f], today=date(2026, 11, 7))
    assert env.extract_calls == 3 and out["firms_failed"] == ["Acme"]
    env.polite.text = "changed"
    env.extract_result = [posting()]
    out = env.go([f], today=date(2026, 11, 8))
    assert env.extract_calls == 4 and out["firms_failed"] == [] and len(out["roles"]) == 1


def test_page_extract_4xx_advances_counter_and_continues(env):
    f = firm(source="page")
    req = httpx.Request("POST", "https://api.test")
    env.extract_result = _raise_budget(anthropic.BadRequestError("x", response=httpx.Response(400, request=req), body=None))
    for d in (4, 5, 6):
        env.go([f], today=date(2026, 11, d))
    assert env.extract_calls == 3
    env.go([f], today=date(2026, 11, 7))
    assert env.extract_calls == 3


def test_page_transient_extract_none_is_not_a_strike(env):
    f = firm(source="page")
    env.extract_result = None
    for d in (4, 5, 6, 7):
        out = env.go([f], today=date(2026, 11, d))
    assert env.extract_calls == 4 and out["firms_failed"] == ["Acme"]


def test_page_failure_counter_is_consecutive(env):
    f = firm(source="page")
    env.extract_result = _raise_budget(AIUnusable("bad"))
    env.go([f])
    env.extract_result = [posting()]
    env.go([f], today=date(2026, 11, 5))
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["fail_count"] == 0


def test_empty_page_closes_after_three_runs_without_ai(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    env.polite.text = "no vacancies"
    env.extract_result = []
    statuses = []
    for d in range(5, 9):
        out = env.go([f], today=date(2026, 11, d))
        statuses.append(out["roles"][0]["status"])
    assert statuses == ["open", "open", "closed", "closed"]
    assert env.extract_calls == 2  # initial read + the one empty read


def test_failed_page_read_closes_nothing(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.go([f])
    env.polite.text = "changed"
    env.extract_result = _raise_budget(AIUnusable("bad"))
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


def _bad(code):
    req = httpx.Request("POST", "https://api.test")
    return anthropic.APIStatusError("x", response=httpx.Response(code, request=req), body=None) if code != 401 else \
        anthropic.AuthenticationError("x", response=httpx.Response(401, request=req), body=None)


def test_ai_4xx_keeps_partial_progress_counts_strike_and_continues(env):
    def res(p):
        if p.url.endswith("/2"):
            raise anthropic.BadRequestError("long", response=httpx.Response(400, request=httpx.Request("POST", "https://a.test")), body=None)
        return enr()

    env.feed = {"bad": [posting("bad", 1), posting("bad", 2), posting("bad", 3)], "acme": [posting()]}
    env.enrich_result = res
    out = env.go([firm("bad"), firm("acme")])
    assert out["firms_failed"] == ["Bad"] and len(out["roles"]) == 3
    st = json.loads((env.data / "firm_state.json").read_text())["bad"]
    assert list(st["role_failures"].values()) == [1]
    assert not env.exit_failed
    for d in (5, 6, 7):
        env.go([firm("bad"), firm("acme")], today=date(2026, 11, d))
    assert sum(u.endswith("/2") for u in env.enrich_calls) == 3  # not re-billed forever


def test_auth_error_exits_nonzero_after_saving(env):
    env.feed = {"bad": [posting("bad")], "acme": [posting()]}
    env.enrich_result = lambda p: (_ for _ in ()).throw(_bad(401)) if p.firm_id == "bad" else enr()
    out = env.go([firm("bad"), firm("acme")])
    assert env.exit_failed and out["roles"] == []  # site data written; no further AI calls after the bad key
    assert env.enrich_calls == ["https://bad.test/1"]


def test_every_firm_failing_exits_nonzero(env):
    env.feed = {"a": RuntimeError("x"), "b": RuntimeError("y")}
    env.go([firm("a"), firm("b")])
    assert env.exit_failed


def test_exception_is_logged_with_firm_id(env, caplog):
    env.feed = {"bad": RuntimeError("boom"), "acme": [posting()]}
    with caplog.at_level("ERROR"):
        env.go([firm("bad"), firm("acme")])
    assert any("bad" in r.getMessage() and r.exc_info for r in caplog.records)


def test_firms_unread_counts_budget_skipped_pages(env):
    env.data.mkdir(parents=True)
    (env.data / "spend.json").write_text(json.dumps({"2026-11": 4.95}))
    env.feed = {"acme": [posting()], "bad": RuntimeError("x")}
    out = env.go([firm("acme"), firm("bad"), firm("pg", source="page")])
    assert (out["firms_checked"], len(out["firms_failed"]), out["firms_unread"]) == (1, 1, 1)
    assert env.extract_calls == 0 and not env.exit_failed


def test_expired_role_closed_on_does_not_churn(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = lambda p: enr().model_copy(update={"closes": date(2026, 11, 1)})
    env.go([firm()])
    out = env.go([firm()], today=date(2026, 11, 5))
    assert out["roles"][0]["status"] == "closed" and out["roles"][0]["closed_on"] == "2026-11-04"


def test_main_exits_nonzero_when_all_firms_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "firms").mkdir()
    (tmp_path / "firms" / "firms.yaml").write_text(
        "firms:\n  - {id: acme, name: Acme, sector: banking, country: CH, source: greenhouse, slug: acme}\n")
    monkeypatch.setattr(daily.anthropic, "Anthropic", lambda: object())
    monkeypatch.setattr(daily, "fetch_feed", lambda f, h: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(SystemExit) as ei:
        daily.main()
    assert ei.value.code == 1
    assert (tmp_path / "site" / "data" / "roles.json").exists()


def test_main_succeeds_and_builds_polite_feed_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "firms").mkdir()
    (tmp_path / "firms" / "firms.yaml").write_text(
        "- {id: acme, name: Acme, sector: banking, country: CH, source: greenhouse, slug: acme}\n")
    monkeypatch.setattr(daily.anthropic, "Anthropic", lambda: object())
    seen = {}

    def feed(f, http):
        seen["ua"] = http.headers["User-Agent"]
        seen["hooks"] = http.event_hooks
        return []

    monkeypatch.setattr(daily, "fetch_feed", feed)
    daily.main()
    assert seen["ua"] == "europe-internship-tracker/1.0"
    assert seen["hooks"]["request"] and seen["hooks"]["response"]


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


def _api(code, msg="x"):
    req = httpx.Request("POST", "https://api.test")
    return anthropic.APIStatusError(msg, response=httpx.Response(code, request=req), body=None)


def test_unusable_posting_on_unchanged_page_reaches_three_strikes_and_stays_listed(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6):
        out = env.go([f], today=date(2026, 11, d))
        assert out["firms_failed"] == ["Acme"]
    assert len(env.enrich_calls) == 3 and env.extract_calls == 3
    out = env.go([f], today=date(2026, 11, 7))
    assert len(env.enrich_calls) == 3 and out["firms_failed"] == ["Acme"]
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert len(st["given_up"]) == 1 and st["role_failures"] == {}


def test_day_30_retry_gets_three_tries_and_stays_listed(env):
    f = firm(source="page")
    env.extract_result = [posting()]
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6):
        env.go([f], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 3
    for i, day in enumerate((date(2026, 12, 6), date(2026, 12, 7), date(2026, 12, 8))):
        out = env.go([f], today=day)
        assert out["firms_failed"] == ["Acme"] and len(env.enrich_calls) == 4 + i
    out = env.go([f], today=date(2026, 12, 9))
    assert len(env.enrich_calls) == 6 and out["firms_failed"] == ["Acme"]


@pytest.mark.parametrize("err", [_api(401), _api(403), _api(404), _api(400, "Your credit balance is too low")])
def test_account_errors_never_strike_and_roles_appear_after_fix(env, err):
    env.feed = {"acme": [posting()]}
    env.enrich_result = _raise(err)
    for d in (4, 5, 6):
        out = env.go([firm()], today=date(2026, 11, d))
        assert env.exit_failed and out["roles"] == []
        env.exit_failed = False
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["given_up"] == {} and st["role_failures"] == {} and st["rejected"] == []
    env.enrich_result = lambda p: enr()
    out = env.go([firm()], today=date(2026, 11, 7))
    assert len(out["roles"]) == 1 and not env.exit_failed


def test_account_error_on_page_extraction_is_not_a_strike(env):
    f = firm(source="page")
    env.extract_result = _raise_budget(_api(401))
    for d in (4, 5, 6, 7):
        env.go([f], today=date(2026, 11, d))
    assert env.extract_calls == 4 and env.exit_failed
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert st["gave_up_hash"] is None and st["fail_count"] == 0


def test_account_error_stops_ai_but_feeds_still_read(env):
    env.feed = {"bad": [posting("bad")], "acme": [posting()]}
    env.enrich_result = _raise(_api(401))
    out = env.go([firm("bad"), firm("acme")])
    assert env.enrich_calls == ["https://bad.test/1"] and env.exit_failed and out["roles"] == []
    st = json.loads((env.data / "firm_state.json").read_text())
    assert "acme" in st


def test_input_specific_413_is_a_strike(env):
    env.feed = {"acme": [posting()]}
    env.enrich_result = _raise(_api(413))
    for d in (4, 5, 6):
        env.go([firm()], today=date(2026, 11, d))
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert len(st["given_up"]) == 1 and st["role_failures"] == {}


def _giveup_then_day30(env):
    """Give up on one feed posting (days 4-6), return the day-30 retry date."""
    env.feed = {"acme": [posting()], "bad": [posting("bad")]}
    env.enrich_result = _raise(AIUnusable("bad"))
    for d in (4, 5, 6):
        env.go([firm()], today=date(2026, 11, d))
    assert len(env.enrich_calls) == 3
    return date(2026, 12, 6)


@pytest.mark.parametrize("case", ["account_error", "blocked", "transient_none", "budget_stop"])
def test_day_30_retry_interrupted_keeps_given_up_and_listing(env, case):
    day = _giveup_then_day30(env)
    firms = [firm()]
    if case == "account_error":
        env.enrich_result = _raise(_api(401))
    elif case == "blocked":
        firms = [firm("bad"), firm()]
        env.enrich_result = lambda p: (_ for _ in ()).throw(_api(401)) if p.firm_id == "bad" else enr()
    elif case == "transient_none":
        env.enrich_result = lambda p: None
    else:
        def stop(p):
            env_budget[0].exhausted = True
            return None
        env_budget = []
        orig = daily.Budget

        def mk(*a, **k):
            b = orig(*a, **k)
            env_budget.append(b)
            return b

        daily.Budget = mk
        env.enrich_result = stop
    try:
        out = env.go(firms, today=day)
    finally:
        if case == "budget_stop":
            daily.Budget = orig
    st = json.loads((env.data / "firm_state.json").read_text())["acme"]
    assert len(st["given_up"]) == 1 and st["role_failures"] == {} and st["rejected"] == []
    assert "Acme" in out["firms_failed"] and out["roles"] == []
    # later runs still treat it as pending and listed, then recovery shows the role
    env.enrich_result = lambda p: None if case == "transient_none" else enr()
    out = env.go([firm()], today=date(2026, 12, 7))
    if case == "transient_none":
        assert "Acme" in out["firms_failed"]
        env.enrich_result = lambda p: enr()
        out = env.go([firm()], today=date(2026, 12, 8))
    assert len(out["roles"]) == 1 and out["firms_failed"] == []


def test_firms_not_covered_is_written_and_kept_out_of_the_counts(env):
    env.feed = {"acme": [posting()]}
    out = env.go([firm()])
    assert out["firms_not_covered"] == []  # default: nothing listed
    daily.run([firm()], env.data, env.site, TODAY, client=object(), http=object(), polite=env.polite,
              not_covered=["UBS", "EFG"])
    out = json.loads((env.site / "data" / "roles.json").read_text())
    assert out["firms_not_covered"] == ["EFG", "UBS"]
    assert out["firms_failed"] == [] and out["firms_checked"] == 1 and out["firms_unread"] == 0


def test_load_not_covered(tmp_path):
    assert daily._load_not_covered(tmp_path / "missing.yaml") == []
    (tmp_path / "b.yaml").write_text("- UBS\n- BNP Paribas\n")
    assert daily._load_not_covered(tmp_path / "b.yaml") == ["UBS", "BNP Paribas"]
    (tmp_path / "e.yaml").write_text("")
    assert daily._load_not_covered(tmp_path / "e.yaml") == []
    (tmp_path / "bad.yaml").write_text("a: b\n")
    with pytest.raises(ValueError):
        daily._load_not_covered(tmp_path / "bad.yaml")


def test_shipped_blocked_list_parses():
    from pathlib import Path
    names = daily._load_not_covered(Path(__file__).parent.parent / "firms" / "blocked.yaml")
    assert len(names) == 15 and "UBS" in names and "Neuberger Berman" in names


def _two_firms_env(env, ticks):
    env.feed = {"acme": [posting("acme")], "beta": [posting("beta")]}
    it = iter(ticks)
    last = [0]

    def clock():
        last[0] = next(it, last[0])
        return last[0]
    return clock


def test_deadline_stops_new_firms_writes_site_and_a_later_run_finishes(env):
    firms = [firm("acme"), firm("beta")]
    # started=0; before acme 0 (ok); before beta 3000s > 45 min -> stop
    clock = _two_firms_env(env, [0, 0, 3000])
    daily.run(firms, env.data, env.site, TODAY, client=object(), http=object(), polite=env.polite, clock=clock)
    out = json.loads((env.site / "data" / "roles.json").read_text())
    assert [r["firm_id"] for r in out["roles"]] == ["acme"]
    assert (out["firms_checked"], out["firms_unread"], out["firms_failed"]) == (1, 1, [])
    # next run: acme was checked, beta never, so beta goes first and the run reaches both
    out = env.go(firms)
    assert sorted(r["firm_id"] for r in out["roles"]) == ["acme", "beta"]
    assert (out["firms_checked"], out["firms_unread"]) == (2, 0)


def test_deadline_zero_reads_nothing_but_still_writes_site(env):
    env.feed = {"acme": [posting()]}
    daily.run([firm()], env.data, env.site, TODAY, client=object(), http=object(), polite=env.polite,
              deadline_minutes=0)
    out = json.loads((env.site / "data" / "roles.json").read_text())
    assert out["roles"] == [] and out["firms_unread"] == 1 and out["firms_checked"] == 0


def test_never_checked_firms_first_then_oldest_with_list_order_ties(env):
    from tracker.merge import FirmState
    env.data.mkdir(parents=True)
    states = {"a": FirmState(last_checked=date(2026, 11, 3)), "b": FirmState(last_checked=date(2026, 10, 1)),
              "c": FirmState(last_checked=date(2026, 10, 1))}
    (env.data / "firm_state.json").write_text(
        json.dumps({k: v.model_dump(mode="json") for k, v in states.items()}))
    seen = []
    env.feed = {k: [] for k in "abcde"}
    orig = daily.fetch_feed

    def spy(f, http):
        seen.append(f.id)
        return orig(f, http)
    import pytest as _p
    mp = _p.MonkeyPatch()
    mp.setattr(daily, "fetch_feed", spy)
    try:
        env.go([firm(i) for i in "abcde"])  # d, e never checked
    finally:
        mp.undo()
    assert seen == ["d", "e", "b", "c", "a"]
