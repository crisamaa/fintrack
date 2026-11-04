from datetime import date, timedelta

from tracker.merge import FirmResult, FirmState, close_expired, merge
from tracker.models import Enrichment, Firm, Posting, Role, role_id

TODAY = date(2026, 11, 4)
FIRM = Firm(id="acme", name="Acme", sector="banking", country="CH", source="greenhouse", slug="acme")


def posting(n=1):
    return Posting(firm_id="acme", title=f"Intern {n}", url=f"https://x.test/{n}", location="Zurich", description="d")


def enr(**kw):
    base = dict(is_internship=True, countries=["CH"], city="Zurich", season="summer", year=2027,
                closes=None, languages_required=["en"])
    base.update(kw)
    return Enrichment(**base)


def role(n=1, **kw):
    p = posting(n)
    base = dict(id=role_id("acme", p.url), firm_id="acme", firm_name="Acme", sector="banking", title=p.title,
                url=p.url, city="Zurich", countries=["CH"], season="summer", year=2027, closes=None,
                languages_required=["en"], status="open", first_seen=date(2026, 10, 1),
                last_seen=date(2026, 11, 3), last_checked=date(2026, 11, 3))
    base.update(kw)
    return Role(**base)


def ok(*ps):
    return FirmResult(firm_id="acme", status="ok", postings=list(ps))


def test_new_posting_with_enrichment_is_added():
    p = posting(1)
    roles, state = merge({}, ok(p), FIRM, FirmState(), {role_id("acme", p.url): enr()}, TODAY)
    r = roles[role_id("acme", p.url)]
    assert r.status == "open" and r.first_seen == TODAY and r.city == "Zurich" and r.firm_name == "Acme"
    assert state.last_checked == TODAY


def test_posting_without_enrichment_is_not_added():
    roles, _ = merge({}, ok(posting(1)), FIRM, FirmState(), {}, TODAY)
    assert roles == {}


def test_missing_role_closes_when_firm_ok():
    r1, r2 = role(1), role(2)
    roles, _ = merge({r1.id: r1, r2.id: r2}, ok(posting(1)), FIRM, FirmState(), {}, TODAY)
    assert roles[r1.id].status == "open" and roles[r1.id].last_seen == TODAY
    assert roles[r2.id].status == "closed" and roles[r2.id].closed_on == TODAY


def test_failed_firm_closes_nothing():
    r1 = role(1)
    state = FirmState(last_checked=date(2026, 11, 3))
    roles, st = merge({r1.id: r1}, FirmResult(firm_id="acme", status="failed", postings=[]), FIRM, state, {}, TODAY)
    assert roles[r1.id].status == "open" and roles[r1.id].last_checked == date(2026, 11, 3)
    assert st.last_checked == date(2026, 11, 3)


def test_zero_results_need_three_runs_to_close():
    r1 = role(1)
    roles, state = {r1.id: r1}, FirmState()
    for i in (1, 2):
        roles, state = merge(roles, ok(), FIRM, state, {}, TODAY)
        assert roles[r1.id].status == "open" and state.zero_streak == i
    roles, state = merge(roles, ok(), FIRM, state, {}, TODAY)
    assert roles[r1.id].status == "closed" and roles[r1.id].closed_on == TODAY


def test_zero_streak_resets_on_any_result():
    r1 = role(1)
    roles, state = merge({r1.id: r1}, ok(), FIRM, FirmState(zero_streak=2), {}, TODAY)
    assert state.zero_streak == 3  # sanity: third empty run
    roles, state = merge({r1.id: role(1)}, ok(posting(1)), FIRM, FirmState(zero_streak=2), {}, TODAY)
    assert state.zero_streak == 0 and roles[r1.id].status == "open"


def test_returning_role_reopens():
    r1 = role(1, status="closed", closed_on=date(2026, 11, 1))
    roles, _ = merge({r1.id: r1}, ok(posting(1)), FIRM, FirmState(), {}, TODAY)
    assert roles[r1.id].status == "open" and roles[r1.id].closed_on is None


def test_closing_date_passed_closes():
    r1 = role(1, closes=TODAY - timedelta(days=1))
    out = close_expired({r1.id: r1}, TODAY)
    assert out[r1.id].status == "closed" and out[r1.id].closed_on == TODAY


def test_closing_today_still_open():
    r1 = role(1, closes=TODAY)
    assert close_expired({r1.id: r1}, TODAY)[r1.id].status == "open"


def test_no_closing_date_stays_open():
    r1 = role(1, closes=None, last_seen=date(2020, 1, 1))
    assert close_expired({r1.id: r1}, TODAY)[r1.id].status == "open"


def test_unchanged_updates_last_checked_and_closes_nothing():
    r1 = role(1)
    res = FirmResult(firm_id="acme", status="unchanged", postings=[])
    roles, st = merge({r1.id: r1}, res, FIRM, FirmState(), {}, TODAY)
    assert roles[r1.id].status == "open" and roles[r1.id].last_checked == TODAY
    assert st.last_checked == TODAY and st.zero_streak == 0


def test_unchanged_after_empty_run_counts_as_another_empty_run():
    r1 = role(1)
    res = FirmResult(firm_id="acme", status="unchanged", postings=[])
    roles, st = {r1.id: r1}, FirmState(zero_streak=1)
    roles, st = merge(roles, res, FIRM, st, {}, TODAY)
    assert st.zero_streak == 2 and roles[r1.id].status == "open"
    roles, st = merge(roles, res, FIRM, st, {}, TODAY)
    assert st.zero_streak == 3 and roles[r1.id].status == "closed" and roles[r1.id].closed_on == TODAY


def test_expired_role_is_not_reopened_and_keeps_closed_on():
    r1 = role(1, closes=TODAY - timedelta(days=3), status="closed", closed_on=TODAY - timedelta(days=1))
    roles, _ = merge({r1.id: r1}, ok(posting(1)), FIRM, FirmState(), {}, TODAY)
    assert roles[r1.id].status == "closed" and roles[r1.id].closed_on == TODAY - timedelta(days=1)


def test_expired_role_closed_on_stable_over_two_runs():
    r1 = role(1, closes=TODAY - timedelta(days=1))
    roles, state = {r1.id: r1}, FirmState()
    for d in (0, 1):
        day = TODAY + timedelta(days=d)
        roles, state = merge(roles, ok(posting(1)), FIRM, state, {}, day)
        roles = close_expired(roles, day)
        assert roles[r1.id].status == "closed" and roles[r1.id].closed_on == TODAY


def test_merge_does_not_mutate_inputs():
    r1 = role(1)
    state = FirmState()
    merge({r1.id: r1}, ok(), FIRM, state, {}, TODAY)
    assert r1.last_checked == date(2026, 11, 3) and state.last_checked is None


def test_state_loads_with_missing_fields():
    s = FirmState.model_validate({"hash": "h"})
    assert s.zero_streak == 0 and s.rejected == [] and s.role_failures == {} and s.given_up == {}
