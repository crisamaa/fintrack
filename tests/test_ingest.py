import json

from conftest import make_ctx
from helpers import employer, later, opp_for, posting, result, row, source
from tracker import facts, ingest, lifecycle
from tracker.connectors import RawPosting, SourceRef
from tracker.db import one
from tracker.http import SourceFailure
import pytest


def setup():
    ctx = make_ctx()
    employer(ctx)
    sid = source(ctx)
    return ctx, sid


def check(ctx, sid, *postings, **kw):
    return ingest.ingest(ctx, row(ctx, sid), result(*postings, **kw))


def count(ctx, table):
    return ctx.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# --- Workflow B: new vacancy detection -------------------------------------------------------------------------

def test_new_vacancy_is_collected_normalised_classified_and_open():
    ctx, sid = setup()
    out = check(ctx, sid, posting(1))
    assert out.new_opportunities == 1
    o = opp_for(ctx, 1)
    assert o["status"] == "open" and o["discovered_via"] == "monitoring"
    assert o["city"] == "Zurich" and json.loads(o["countries"]) == ["CH"]
    assert o["internship_type"] == "summer" and o["intake_year"] == 2027
    assert set(json.loads(o["sectors"])) >= {"m_and_a", "investment_banking"}
    assert json.loads(o["languages"]) == ["de", "en"]
    kinds = {r["kind"] for r in ctx.conn.execute("SELECT kind FROM opportunity_requirements")}
    assert {"work_authorization", "language"} <= kinds
    evidence = one(ctx.conn, "SELECT evidence FROM opportunity_requirements WHERE kind = 'work_authorization'")[0]
    assert "right to work in Switzerland" in evidence
    assert one(ctx.conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'enrich'")[0] == 1
    assert one(ctx.conn, "SELECT COUNT(*) FROM notifications WHERE kind = 'new_opportunity'")[0] == 1
    s = row(ctx, sid)
    assert s["health"] == "ok" and s["postings_total"] == 1 and s["next_check_at"] > s["last_success_at"]


def test_same_check_twice_is_idempotent():
    ctx, sid = setup()
    check(ctx, sid, posting(1), posting(2))
    before = {t: count(ctx, t) for t in ("opportunities", "source_postings", "opportunity_facts",
                                         "opportunity_requirements", "notifications", "jobs")}
    later(ctx, hours=8)
    out = check(ctx, sid, posting(1), posting(2))
    assert out.new_opportunities == 0 and out.new_postings == 0
    assert {t: count(ctx, t) for t in before} == before
    events = count(ctx, "opportunity_events")
    later(ctx, hours=8)
    check(ctx, sid, posting(1), posting(2))
    assert count(ctx, "opportunity_events") == events


def test_removed_posting_closes_with_evidence_and_reopens():
    ctx, sid = setup()
    check(ctx, sid, posting(1), posting(2))
    later(ctx, days=1)
    out = check(ctx, sid, posting(1))
    assert out.closed == 1
    o = opp_for(ctx, 2)
    assert o["status"] == "closed" and "complete check" in o["close_reason"]
    later(ctx, days=1)
    check(ctx, sid, posting(1), posting(2))
    o = opp_for(ctx, 2)
    assert o["status"] == "open" and o["reopened_count"] == 1 and o["closed_at"] is None
    assert one(ctx.conn, "SELECT COUNT(*) FROM opportunity_events WHERE event = 'reopened'")[0] == 1


def test_changed_deadline_is_recorded_as_a_change():
    ctx, sid = setup()
    check(ctx, sid, posting(1, structured={"valid_through": "2026-12-01"}))
    later(ctx, days=1)
    check(ctx, sid, posting(1, structured={"valid_through": "2026-12-15"}, description="Changed text."))
    assert opp_for(ctx, 1)["closes_on"] == "2026-12-15"
    ev = one(ctx.conn, "SELECT old_value, new_value FROM opportunity_events WHERE event = 'changed' AND "
             "field = 'closes_on'")
    assert tuple(ev) == ("2026-12-01", "2026-12-15")


def test_incomplete_read_never_closes_anything():
    ctx, sid = setup()
    check(ctx, sid, posting(1), posting(2))
    ingest.ingest(ctx, row(ctx, sid), result(posting(1), complete=False))
    assert opp_for(ctx, 2)["status"] == "open"


def test_empty_board_must_be_empty_three_times():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    for i in range(2):
        later(ctx, days=1)
        check(ctx, sid, total_listed=0)
        assert opp_for(ctx, 1)["status"] == "open", f"closed after {i + 1} empty checks"
    later(ctx, days=1)
    check(ctx, sid, total_listed=0)
    assert opp_for(ctx, 1)["status"] == "closed"


def test_board_with_other_jobs_but_no_internships_closes_at_once():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    later(ctx, days=1)
    check(ctx, sid, total_listed=12)  # the board answered with 12 jobs, none an internship: real evidence
    assert opp_for(ctx, 1)["status"] == "closed"


def test_ai_extracted_postings_need_two_misses():
    ctx = make_ctx()
    employer(ctx)
    sid = source(ctx, kind="page", url="https://www.acmebank.ch/careers")
    p = lambda n: posting(n, extraction="ai")  # noqa: E731
    check(ctx, sid, p(1), p(2))
    later(ctx, days=1)
    check(ctx, sid, p(1))
    assert opp_for(ctx, 2, sid)["status"] == "open"
    later(ctx, days=1)
    check(ctx, sid, p(1))
    assert opp_for(ctx, 2, sid)["status"] == "closed"


def test_not_modified_confirms_listed_postings():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    later(ctx, days=1)
    ingest.ingest(ctx, row(ctx, sid), result(not_modified=True))
    o = opp_for(ctx, 1)
    assert o["status"] == "open" and o["last_verified_at"] == "2026-11-06T05:00:00Z"


def test_outside_countries_is_excluded_not_deleted():
    ctx, sid = setup()
    check(ctx, sid, posting(1, location="New York, NY", description="Summer intern in New York."))
    o = opp_for(ctx, 1)
    assert o["excluded_reason"] == "outside_countries" and o["status"] == "open"


def test_outside_countries_with_sponsorship_is_kept():
    ctx, sid = setup()
    check(ctx, sid, posting(1, location="New York, NY", description="Visa sponsorship is available for this role."))
    assert opp_for(ctx, 1)["excluded_reason"] is None


# --- Workflow H: source failure and recovery -------------------------------------------------------------------

def test_failures_are_recorded_and_never_close_vacancies():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    for i, kind in enumerate(["unavailable", "unavailable", "unavailable"]):
        later(ctx, hours=6)
        ingest.record_failure(ctx, row(ctx, sid), SourceFailure(kind, "503 from board", 503))
    s = row(ctx, sid)
    assert s["health"] == "failing" and s["consecutive_failures"] == 3 and s["checks_failed"] == 3
    assert opp_for(ctx, 1)["status"] == "open"
    assert one(ctx.conn, "SELECT COUNT(*) FROM source_checks WHERE outcome = 'unavailable'")[0] == 3
    later(ctx, hours=6)
    check(ctx, sid, posting(1))
    s = row(ctx, sid)
    assert s["health"] == "ok" and s["consecutive_failures"] == 0 and opp_for(ctx, 1)["status"] == "open"


def test_backoff_grows_and_blocked_sources_wait_a_week():
    ctx, sid = setup()
    times = []
    for _ in range(3):
        ingest.record_failure(ctx, row(ctx, sid), SourceFailure("unavailable", "x"))
        times.append(row(ctx, sid)["next_check_at"])
    assert times == sorted(times) and len(set(times)) == 3
    ingest.record_failure(ctx, row(ctx, sid), SourceFailure("blocked", "403"))
    s = row(ctx, sid)
    assert s["health"] == "blocked" and s["next_check_at"] >= "2026-11-11"


def test_long_failure_marks_unavailable_not_closed():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    later(ctx, days=8)
    for _ in range(3):
        ingest.record_failure(ctx, row(ctx, sid), SourceFailure("unavailable", "down"))
    ingest.maintain(ctx)
    assert opp_for(ctx, 1)["status"] == "unavailable"
    check(ctx, sid, posting(1))
    assert opp_for(ctx, 1)["status"] == "open"


def test_expired_by_closing_date():
    ctx, sid = setup()
    check(ctx, sid, posting(1, structured={"valid_through": "2026-11-10"}))
    later(ctx, days=10)
    ingest.maintain(ctx)
    assert opp_for(ctx, 1)["status"] == "expired"


def test_important_source_failure_notifies_once():
    ctx = make_ctx()
    employer(ctx, priority=1)
    sid = source(ctx, priority=1)
    for _ in range(5):
        ingest.record_failure(ctx, row(ctx, sid), SourceFailure("unavailable", "down"))
    assert one(ctx.conn, "SELECT COUNT(*) FROM notifications WHERE kind = 'source_problem'")[0] == 1


# --- dedup and provenance --------------------------------------------------------------------------------------

def test_same_role_on_two_sources_is_one_opportunity():
    ctx, sid = setup()
    page = source(ctx, kind="page", url="https://www.acmebank.ch/careers")
    check(ctx, sid, posting(1, title="Summer Intern M&A (m/w/d)"))
    other = RawPosting(external_id="X-9", title="Summer Intern M&A", url="https://www.acmebank.ch/careers/9",
                       location="Zurich", description="Same role", extraction="jsonld")
    ingest.ingest(ctx, row(ctx, page), result(other))
    assert count(ctx, "opportunities") == 1
    assert one(ctx.conn, "SELECT COUNT(DISTINCT source_id) FROM source_postings")[0] == 2


def test_same_title_in_two_cities_stays_two():
    ctx, sid = setup()
    check(ctx, sid, posting(1, title="Summer Intern"), posting(2, title="Summer Intern", location="Geneva"))
    assert count(ctx, "opportunities") == 2


def test_similar_title_is_flagged_not_merged():
    ctx, sid = setup()
    page = source(ctx, kind="page", url="https://www.acmebank.ch/careers")
    check(ctx, sid, posting(1, title="Summer Internship Investment Banking"))
    near = RawPosting(external_id="Y", title="Summer Internships Investment Banking", url="https://www.acmebank.ch/c/y",
                      location="Zurich", extraction="jsonld")
    ingest.ingest(ctx, row(ctx, page), result(near))
    assert count(ctx, "opportunities") == 2
    flagged = one(ctx.conn, "SELECT review_flag FROM opportunities WHERE review_flag IS NOT NULL")[0]
    assert flagged.startswith("possible_duplicate:")


def test_tracking_parameters_do_not_create_duplicates():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    page = source(ctx, kind="page", url="https://www.acmebank.ch/careers")
    p = posting(1, title="Different wording here")
    p.url = "https://boards.greenhouse.io/acme/jobs/1?utm_source=newsletter&gh_src=abc"
    p.external_id = "other-id"
    ingest.ingest(ctx, row(ctx, page), result(p))
    assert count(ctx, "opportunities") == 1


def test_ai_fact_cannot_overwrite_structured_fact():
    ctx, sid = setup()
    check(ctx, sid, posting(1, structured={"valid_through": "2026-12-01"}))
    oid = opp_for(ctx, 1)["id"]
    facts.set_fact(ctx.conn, ctx.now, oid, "closes_on", "2027-03-01", "ai", evidence="model said so")
    assert opp_for(ctx, 1)["closes_on"] == "2026-12-01"
    methods = {r[0] for r in ctx.conn.execute("SELECT method FROM opportunity_facts WHERE field = 'closes_on'")}
    assert methods == {"structured", "ai"}  # both kept for inspection
    facts.set_fact(ctx.conn, ctx.now, oid, "closes_on", "2026-12-20", "manual")
    assert opp_for(ctx, 1)["closes_on"] == "2026-12-20"


def test_illegal_transition_is_refused():
    ctx, sid = setup()
    check(ctx, sid, posting(1))
    oid = opp_for(ctx, 1)["id"]
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.transition(ctx.conn, ctx.now, oid, "closed", "")
    lifecycle.transition(ctx.conn, ctx.now, oid, "closed", "test")
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.transition(ctx.conn, ctx.now, oid, "pending", "back")


# --- Workflow C: cross-source discovery ------------------------------------------------------------------------

def search_source(ctx):
    from tracker import entities
    entities.register_source(ctx.conn, ctx.now, SourceRef("search", "search://gemini", "gemini"), employer_id=None,
                             discovery_method="search", association="unverified", category="aggregator",
                             enabled=False)
    return "search:gemini"


def test_search_find_for_monitored_employer_is_pending_gap_and_queues_source_discovery():
    ctx, sid = setup()
    srch = search_source(ctx)
    p = RawPosting(external_id="https://jobs.example.com/acme/77", title="Off-Cycle Intern Treasury",
                   url="https://jobs.example.com/acme/77", location="Basel", extraction="jsonld",
                   structured={"hiring_organization": "Acme Bank AG"})
    ingest.apply_postings(ctx, row(ctx, srch), [p], complete=False)
    o = dict(one(ctx.conn, "SELECT * FROM opportunities"))
    assert o["employer_id"] == "acme" and o["status"] == "pending" and o["discovered_via"] == "search"
    gap = one(ctx.conn, "SELECT cause, resolved_at FROM coverage_gaps")
    assert tuple(gap) == ("missed_by_monitoring", None)
    assert one(ctx.conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'discover_sources' AND key = 'acme'")[0] == 1
    assert one(ctx.conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'verify'")[0] == 1
    # monitoring later lists the same posting (same URL): confirmed open, gap resolved
    page = source(ctx, kind="page", url="https://www.acmebank.ch/careers")
    same = RawPosting(external_id="77", title="Off-Cycle Intern Treasury", url="https://jobs.example.com/acme/77",
                      location="Basel", extraction="jsonld")
    ingest.ingest(ctx, row(ctx, page), result(same))
    o = dict(one(ctx.conn, "SELECT * FROM opportunities"))
    assert o["status"] == "open"
    assert one(ctx.conn, "SELECT resolved_at FROM coverage_gaps")[0] is not None


def test_unknown_hiring_organisation_becomes_candidate():
    ctx = make_ctx()
    srch = search_source(ctx)
    p = RawPosting(external_id="u1", title="Summer Intern", url="https://jobs.example.com/x/1", location="Lima, Peru",
                   extraction="jsonld", structured={"hiring_organization": "Kallpa Securities SAB",
                                                    "hiring_organization_url": "https://www.kallpasab.com"})
    ingest.apply_postings(ctx, row(ctx, srch), [p], complete=False)
    c = dict(one(ctx.conn, "SELECT * FROM employer_candidates"))
    assert c["name"] == "Kallpa Securities SAB" and c["country"] == "PE" and c["origin"] == "posting"
    assert count(ctx, "opportunities") == 0 and count(ctx, "source_postings") == 1
