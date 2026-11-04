import json
from datetime import timedelta

import httpx

from conftest import FakeProvider, client, make_ctx
from helpers import employer, later, opp_for, posting, result, row, source
from tracker import coverage, discovery, ingest, jobs, programs, prospects, work
from tracker.ai import AITransient
from tracker.config import Config
from tracker.db import iso, one

CAREERS = "https://www.acmebank.ch/careers"
PAGE_TEXT_HTML = ("<html><body><h1>Careers at Acme Bank</h1>" +
                  "<p>Students: <a href='/careers/summer-2027'>Summer Analyst Internship 2027</a> in Zurich. "
                  "Applications close on 15 December 2026.</p>" * 6 + "</body></html>")


def extraction_reply(content):
    return json.dumps({"postings": [
        {"title": "Summer Analyst Internship 2027", "url": "/careers/summer-2027", "location": "Zurich",
         "description": "Ten weeks."},
        {"title": "Invented Hedge Fund Internship", "url": "/x", "location": "Zug", "description": "Not on the page"}]})


def page_ctx(provider=None):
    http = client(lambda req: httpx.Response(200, text=PAGE_TEXT_HTML) if str(req.url) == CAREERS else None)
    ctx = make_ctx(http=http, provider=provider)
    employer(ctx)
    sid = source(ctx, kind="page", url=CAREERS)
    return ctx, sid


# --- jobs -------------------------------------------------------------------------------------------------------

def test_enqueue_is_idempotent_and_refresh_rearms(ctx):
    assert jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1", {"hash": "a"})
    assert not jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1", {"hash": "a"})
    j = jobs.due(ctx.conn, ctx.now, ["enrich"], 10)[0]
    jobs.claim(ctx.conn, ctx.now, j["id"])
    jobs.done(ctx.conn, ctx.now, j["id"])
    assert not jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1", {"hash": "a"})
    assert jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1", {"hash": "b"}, refresh=True)


def test_failures_back_off_then_die(ctx):
    jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1", max_attempts=3)
    waits = []
    for _ in range(3):
        j = one(ctx.conn, "SELECT * FROM jobs")
        jobs.claim(ctx.conn, ctx.now, j["id"])
        status = jobs.fail(ctx.conn, ctx.now, j["id"], "boom")
        waits.append(one(ctx.conn, "SELECT next_attempt_at FROM jobs")[0])
    assert status == "dead" and waits[0] < waits[1]


def test_interrupted_job_is_recovered(ctx):
    jobs.enqueue(ctx.conn, ctx.now, "enrich", "o1")
    j = one(ctx.conn, "SELECT * FROM jobs")
    jobs.claim(ctx.conn, ctx.now, j["id"])
    assert jobs.recover(ctx.conn, ctx.now) == 0  # lease still valid
    assert jobs.recover(ctx.conn, ctx.now + timedelta(hours=1)) == 1
    assert one(ctx.conn, "SELECT status FROM jobs")[0] == "pending"


# --- Workflow I: AI outage or budget --------------------------------------------------------------------------

def test_collection_continues_without_ai_and_work_resumes_later():
    ctx, sid = page_ctx(provider=None)  # no key at all
    ats = source(ctx, board="acme2")
    ctx.http = client(lambda req: (httpx.Response(200, text=PAGE_TEXT_HTML) if str(req.url) == CAREERS else
                                   httpx.Response(200, json={"jobs": [{"id": 5, "title": "Risk Intern",
                                                  "absolute_url": "https://boards.greenhouse.io/acme2/jobs/5",
                                                  "location": {"name": "Zurich"}, "content": ""}]})
                                   if "boards-api" in str(req.url) else None))
    assert work.collect(ctx) == 2
    assert row(ctx, sid)["last_outcome"] == "deferred" and row(ctx, sid)["content_hash"] is None
    assert opp_for(ctx, 5, ats)["status"] == "open"  # deterministic collection was not blocked
    counts = work.drain(ctx)
    assert counts["deferred"] >= 1
    assert one(ctx.conn, "SELECT status FROM jobs WHERE kind = 'extract_page'")[0] == "pending"
    # the model comes back
    prov = FakeProvider({"careers page": extraction_reply})
    ctx2 = make_ctx(ctx.conn, now=ctx.now + timedelta(hours=7), http=ctx.http, provider=prov)
    work.drain(ctx2, ["extract_page"])
    titles = [r[0] for r in ctx2.conn.execute("SELECT title FROM source_postings WHERE source_id = ?", (sid,))]
    assert titles == ["Summer Analyst Internship 2027"]  # the invented posting was discarded
    assert row(ctx2, sid)["content_hash"]  # hash stored only after successful extraction


def test_transient_ai_errors_defer_without_spending_attempts():
    ctx, sid = page_ctx(provider=FakeProvider(default=AITransient("503")))
    work.collect(ctx)
    work.drain(ctx)
    j = one(ctx.conn, "SELECT status, attempts FROM jobs WHERE kind = 'extract_page'")
    assert tuple(j) == ("pending", 0)
    assert one(ctx.conn, "SELECT outcome FROM ai_calls")[0] == "transient"


# --- enrichment ------------------------------------------------------------------------------------------------

def enrichment(**kw):
    base = {"is_internship": True, "countries": ["ch"], "city": "Zurich", "internship_type": "summer",
            "intake_year": 2027, "closes_on": "2026-12-15", "closes_quote": "Applications close on 15 December 2026",
            "languages": ["de"], "requirements": [
                {"kind": "graduation_year", "summary": "graduating 2028", "years": [2028],
                 "quote": "graduating in 2028"},
                {"kind": "sponsorship", "summary": "no visa", "available": False, "quote": "We sponsor every visa"}]}
    base.update(kw)
    return json.dumps(base)


def enrich_ctx(reply):
    ctx = make_ctx(provider=FakeProvider(default=reply))
    employer(ctx)
    sid = source(ctx)
    p = posting(1, title="Intern", location="", description="Intern in our team, graduating in 2028. "
                                                             "Applications close on 15 December 2026.")
    ingest.ingest(ctx, row(ctx, sid), result(p))
    work.drain(ctx, ["enrich"])
    return ctx, opp_for(ctx, 1)


def test_enrichment_keeps_only_evidenced_facts():
    ctx, o = enrich_ctx(enrichment())
    assert json.loads(o["countries"]) == ["CH"] and o["closes_on"] == "2026-12-15" and o["enrichment"] == "done"
    reqs = {r[0]: json.loads(r[1]) for r in ctx.conn.execute("SELECT kind, value FROM opportunity_requirements")}
    assert reqs["graduation_year"] == {"years": [2028]}
    assert "sponsorship" not in reqs  # the model's sponsorship quote is not in the posting, so it was dropped


def test_enrichment_drops_deadline_without_quote():
    _, o = enrich_ctx(enrichment(closes_quote="Deadline: 1 March"))
    assert o["closes_on"] is None


def test_ai_not_internship_excludes_but_keeps():
    _, o = enrich_ctx(enrichment(is_internship=False))
    assert o["excluded_reason"] == "not_internship" and o["status"] == "open"


def test_invalid_ai_reply_fails_the_job_but_keeps_the_opportunity():
    ctx, o = enrich_ctx("garbage")
    assert o["enrichment"] == "pending" and o["status"] == "open"
    assert tuple(one(ctx.conn, "SELECT status, attempts FROM jobs WHERE kind = 'enrich'")) == ("pending", 1)
    for _ in range(5):
        later(ctx, days=3)
        work.drain(ctx, ["enrich"])
    assert one(ctx.conn, "SELECT status FROM jobs WHERE kind = 'enrich'")[0] == "dead"
    assert opp_for(ctx, 1)["enrichment"] == "failed"
    assert one(ctx.conn, "SELECT outcome FROM ai_calls")[0] == "invalid"  # billed usage survived the rollback


# --- Workflow A: new employer discovery ------------------------------------------------------------------------

HOME = ("<html><head><title>Andes Capital Partners</title></head><body><h1>Andes Capital Partners</h1>"
        "<p>Andes Capital Partners is an independent M&A advisory boutique in Lima advising Peruvian companies.</p>"
        "<a href='/trabaja-con-nosotros'>Trabaja con nosotros</a>"
        "<a href='https://jobs.lever.co/andescapital'>Open roles</a></body></html>")
WORK = ("<html><body><p>Programa de prácticas para estudiantes universitarios.</p>"
        "<a href='/practicas'>Prácticas profesionales</a></body></html>")


def discovery_http():
    def handle(req):
        u = str(req.url).split("?")[0]
        return {"https://www.andescapital.pe/": httpx.Response(200, text=HOME),
                "https://www.andescapital.pe": httpx.Response(200, text=HOME),
                "https://www.andescapital.pe/trabaja-con-nosotros": httpx.Response(200, text=WORK)}.get(u)
    return client(handle)


def test_unfamiliar_firm_is_verified_registered_and_its_sources_found():
    cls = json.dumps({"is_finance_employer": True, "official_name": "Andes Capital Partners",
                      "sectors": ["m_and_a", "investment_banking"], "countries": ["PE"], "size_hint": "boutique",
                      "quote": "independent M&A advisory boutique in Lima"})
    ctx = make_ctx(http=discovery_http(), provider=FakeProvider(default=cls))
    from tracker import entities
    entities.add_candidate(ctx.conn, ctx.now, name="Andes Capital Partners", origin="search",
                           website="https://www.andescapital.pe", country="PE")
    counts = discovery.process_candidates(ctx)
    assert counts["accepted"] == 1
    e = dict(one(ctx.conn, "SELECT * FROM employers"))
    assert e["name"] == "Andes Capital Partners" and e["hq_country"] == "PE" and e["size_hint"] == "boutique"
    assert "advisory boutique" in e["discovery_evidence"]
    sectors = {r[0] for r in ctx.conn.execute("SELECT sector FROM employer_sectors")}
    assert sectors == {"m_and_a", "investment_banking"}
    srcs = {(r["kind"], r["category"]) for r in ctx.conn.execute("SELECT kind, category FROM sources")}
    assert ("lever", "careers") in srcs and ("page", "early_careers") in srcs
    assert one(ctx.conn, "SELECT COUNT(*) FROM notifications WHERE kind = 'new_employer'")[0] == 1
    # the same firm found again under another name form is a duplicate, not a new employer
    entities.add_candidate(ctx.conn, ctx.now, name="Andes Capital Partners S.A.C.", origin="directory:x",
                           website="https://www.andescapital.pe")
    assert one(ctx.conn, "SELECT COUNT(*) FROM employer_candidates")[0] == 1  # resolved before it was even added


def test_candidate_whose_site_does_not_name_it_is_not_accepted():
    ctx = make_ctx(http=discovery_http(), provider=FakeProvider(default="{}"))
    from tracker import entities
    entities.add_candidate(ctx.conn, ctx.now, name="Totally Different Bank", origin="search",
                           website="https://www.andescapital.pe")
    assert discovery.process_candidates(ctx)["unresolved"] == 1
    assert one(ctx.conn, "SELECT COUNT(*) FROM employers")[0] == 0


def test_classification_quote_must_be_on_the_page():
    cls = json.dumps({"is_finance_employer": True, "official_name": "x", "sectors": ["fintech"], "countries": [],
                      "size_hint": None, "quote": "the largest bank in the galaxy"})
    ctx = make_ctx(http=discovery_http(), provider=FakeProvider(default=cls))
    from tracker import entities
    entities.add_candidate(ctx.conn, ctx.now, name="Andes Capital Partners", origin="search",
                           website="https://www.andescapital.pe")
    assert discovery.process_candidates(ctx)["unresolved"] == 1


def test_directory_candidate_without_website_waits():
    ctx = make_ctx(http=client(lambda r: None))
    from tracker import entities
    entities.add_candidate(ctx.conn, ctx.now, name="Bank Thalwil Genossenschaft", origin="finma", country="CH",
                           sector_hint="banking")
    assert discovery.process_candidates(ctx)["unresolved"] == 1
    c = one(ctx.conn, "SELECT attempts, next_attempt_at FROM employer_candidates")
    assert c["attempts"] == 1 and c["next_attempt_at"] > iso(ctx.now)


# --- Workflow D: recurring programs ----------------------------------------------------------------------------

def test_program_history_is_kept_and_next_window_estimated_and_monitored_more_often():
    ctx = make_ctx()
    employer(ctx)
    sid = source(ctx)
    early = make_ctx(ctx.conn, now=ctx.now.replace(year=2025, month=9, day=1))
    ingest.ingest(early, row(early, sid), result(posting(1, title="2026 Summer Analyst - M&A",
                                                         published_at="2025-09-01")))
    later(early, days=60)
    ingest.ingest(early, row(early, sid), result(total_listed=3))  # closed
    ingest.ingest(ctx, row(ctx, sid), result(posting(2, title="2027 Summer Analyst - M&A",
                                                     published_at="2026-09-10")))
    progs = ctx.conn.execute("SELECT * FROM programs").fetchall()
    assert len(progs) == 1
    pid = progs[0]["id"]
    wins = {(w["intake_year"], w["kind"]): dict(w) for w in ctx.conn.execute("SELECT * FROM program_windows")}
    assert wins[(2026, "observed")]["opens_on"] == "2025-09-01"
    assert wins[(2027, "observed")]["opens_on"] == "2026-09-10"
    late = make_ctx(ctx.conn, now=ctx.now.replace(year=2027, month=8, day=20))
    programs.estimate(late, pid)
    est = dict(one(late.conn, "SELECT * FROM program_windows WHERE kind = 'estimated'"))
    assert est["intake_year"] == 2028 and est["opens_on"] is None  # a range, never an exact date
    assert est["opens_earliest"] <= "2027-09-05" <= est["opens_latest"]
    assert "not an announced date" in est["evidence"]
    from tracker import schedule
    assert schedule.in_season(late.conn, "acme", late.now)
    assert schedule.interval_hours(late.conn, row(late, sid), late.now) == schedule.SEASON_H


def test_confirmed_dates_need_a_quote_from_the_page():
    reply = json.dumps({"programs": [{"name": "Summer Analyst Programme", "program_type": "summer",
                                      "division": "Investment Banking", "location": "Zurich", "eligibility": None,
                                      "dates": [{"which": "opens", "date": "2027-09-01", "intake_year": 2028,
                                                 "is_past": False, "quote": "Applications open 1 September 2027"},
                                                {"which": "closes", "date": "2027-11-30", "intake_year": 2028,
                                                 "is_past": False, "quote": "made up deadline"}]}]})
    html = ("<html><body>" + "<p>Summer Analyst Programme in Investment Banking, Zurich. Applications open "
            "1 September 2027.</p>" * 6 + "</body></html>")
    url = "https://www.acmebank.ch/students"
    ctx = make_ctx(http=client(lambda r: httpx.Response(200, text=html) if str(r.url) == url else None),
                   provider=FakeProvider({"programmes": reply}, default=json.dumps({"postings": []})))
    employer(ctx)
    source(ctx, kind="page", url=url, category="early_careers")
    work.collect(ctx)
    work.drain(ctx, ["extract_page"])
    w = [dict(r) for r in ctx.conn.execute("SELECT * FROM program_windows")]
    assert len(w) == 1 and w[0]["kind"] == "confirmed" and w[0]["opens_on"] == "2027-09-01"
    assert w[0]["closes_on"] is None


# --- Workflow G: prospects -------------------------------------------------------------------------------------

def test_prospect_is_scored_with_reasons_and_researched_with_grounded_notes():
    home = ("<html><body><h1>Helvetia Family Partners</h1><p>We are a multi-family office in Zug serving "
            "entrepreneurs.</p><a href='/about'>About us</a></body></html>")
    about = "<html><body><p>We welcome speculative applications from students at careers@hfp.example.</p></body></html>"
    pages = {"https://www.hfp.ch": home, "https://www.hfp.ch/": home, "https://www.hfp.ch/about": about}
    reply = json.dumps({"summary": "A multi-family office in Zug.", "application_channel": "careers@hfp.example",
                        "notes": [
                            {"kind": "business", "text": "Multi-family office", "source_url": "https://www.hfp.ch/",
                             "quote": "multi-family office in Zug"},
                            {"kind": "policy", "text": "Takes speculative applications",
                             "source_url": "https://www.hfp.ch/about", "quote": "We welcome speculative applications"},
                            {"kind": "teams", "text": "Has a PE team", "source_url": "https://www.hfp.ch/about",
                             "quote": "our private equity team"},
                            {"kind": "development", "text": "x", "source_url": "https://elsewhere.example/",
                             "quote": "multi-family office in Zug"}]})
    ctx = make_ctx(http=client(lambda r: httpx.Response(200, text=pages[str(r.url)]) if str(r.url) in pages
                               else None), provider=FakeProvider(default=reply))
    employer(ctx, "hfp", "Helvetia Family Partners", website="https://www.hfp.ch")
    ctx.conn.execute("UPDATE employers SET size_hint = 'boutique' WHERE id = 'hfp'")
    assert prospects.score_all(ctx) == 1
    p = dict(one(ctx.conn, "SELECT * FROM prospects"))
    factors = {r["factor"] for r in json.loads(p["reasons"])}
    assert {"small_firm", "home_market", "no_recruitment_channel"} <= factors
    prospects.research_due(ctx)
    work.drain(ctx, ["research"])
    notes = [(r["kind"], r["source_url"]) for r in ctx.conn.execute("SELECT * FROM employer_intel ORDER BY kind")]
    assert notes == [("business", "https://www.hfp.ch/"), ("policy", "https://www.hfp.ch/about")]
    p = dict(one(ctx.conn, "SELECT * FROM prospects"))
    assert p["status"] == "researched" and p["application_channel"] == "careers@hfp.example"


def test_employer_with_open_vacancy_is_not_a_prospect():
    ctx = make_ctx()
    employer(ctx)
    sid = source(ctx)
    ingest.ingest(ctx, row(ctx, sid), result(posting(1)))
    prospects.score_all(ctx)
    assert one(ctx.conn, "SELECT COUNT(*) FROM prospects")[0] == 0


# --- Workflow J: coverage audit --------------------------------------------------------------------------------

def test_audit_measures_recall_and_classifies_misses(tmp_path):
    ctx = make_ctx(config=Config(root=tmp_path))
    employer(ctx)
    sid = source(ctx)
    employer(ctx, "beta", "Beta Partners", website="https://beta.example")
    ingest.ingest(ctx, row(ctx, sid), result(posting(1)))
    bench = tmp_path / "benchmark.yaml"
    bench.write_text(f"""
version: test-1
employers:
  - {{name: Acme Bank}}
  - {{name: Beta Partners}}
  - {{name: Gamma Securities}}
opportunities:
  - {{employer: Acme Bank, title: "2027 Summer Analyst Internship - M&A 1", url: "https://boards.greenhouse.io/acme/jobs/1", verified_on: 2026-11-04, published_on: 2026-11-03}}
  - {{employer: Beta Partners, title: Summer Intern, url: "https://beta.example/jobs/1", verified_on: 2026-11-04}}
  - {{employer: Gamma Securities, title: Analyst Intern, url: "https://gamma.example/j/2", verified_on: 2026-11-04}}
labels:
  - {{opportunity_id: {opp_for(ctx, 1)['id']}, relevant: true}}
""")
    out = coverage.audit(ctx, bench)
    m = out["metrics"]
    assert m["discovery_recall"] == round(1 / 3, 4) and m["discovery_precision"] == 1.0
    assert m["source_coverage"] == round(1 / 3, 4) and m["detection_latency_days_median"] == 2
    causes = {x["employer"]: x["cause"] for x in out["misses"]}
    assert causes == {"Beta Partners": "missing_source", "Gamma Securities": "unknown_employer"}
    assert one(ctx.conn, "SELECT COUNT(*) FROM jobs WHERE kind = 'discover_sources' AND key = 'beta'")[0] == 1
    assert one(ctx.conn, "SELECT name FROM employer_candidates")[0] == "Gamma Securities"
    assert one(ctx.conn, "SELECT COUNT(*) FROM coverage_evaluations")[0] == 1


def test_audit_without_benchmark_reports_nulls_not_numbers(tmp_path):
    ctx = make_ctx()
    m = coverage.audit(ctx, tmp_path / "missing.yaml")["metrics"]
    assert m["discovery_recall"] is None and m["discovery_precision"] is None
    assert "no verified opportunities" in m["notes"]["discovery_recall"]


def test_same_name_abroad_is_a_subsidiary_not_the_same_employer():
    from tracker import entities
    ctx = make_ctx()
    employer(ctx, "bbva", "BBVA", website="https://www.bbva.com", country="ES")
    assert entities.resolve(ctx.conn, "BBVA Perú", country="PE") is None
    assert entities.resolve(ctx.conn, "BBVA S.A.", country="ES") == "bbva"
    assert entities.parent_candidate(ctx.conn, "BBVA Perú", "PE") == "bbva"


def test_generic_firm_is_not_a_prospect():
    ctx = make_ctx()
    employer(ctx)
    source(ctx)
    prospects.score_all(ctx)
    assert one(ctx.conn, "SELECT COUNT(*) FROM prospects")[0] == 0
