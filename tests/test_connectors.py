"""Connector contract tests: every connector returns RawPostings for internships only, and turns every kind of
trouble into a SourceFailure with the right kind."""
import json

import httpx
import pytest

from conftest import FIX, client, fixture_json
from tracker import connectors
from tracker.connectors import SourceRef
from tracker.connectors.page import CareersPage
from tracker.http import SourceFailure


def src(kind, board="acme", site=None, url="https://x.example/", **kw):
    return {"id": f"{kind}:{board}", "kind": kind, "board": board, "site": site, "url": url, **kw}


def serve(routes):
    def handler(request):
        key = (request.method, str(request.url).split("?")[0])
        if key in routes:
            r = routes[key]
            return r(request) if callable(r) else r
        return None
    return client(handler)


def fetch(kind, routes, **kw):
    return connectors.get(kind).fetch(src(kind, **kw), serve(routes))


def test_greenhouse_keeps_only_internships():
    r = fetch("greenhouse", {("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
                             httpx.Response(200, json=fixture_json("greenhouse.json"))})
    assert [p.title for p in r.postings] == ["Data Analyst, Intern"]
    p = r.postings[0]
    assert p.url.startswith("https://") and p.location == "Toronto"
    assert "<" not in p.description and "Who we are" in p.description
    assert r.complete and r.total_listed > 1 and p.external_id


def test_lever_keeps_only_internships_and_joins_locations():
    data = fixture_json("lever.json")
    data[0]["categories"]["allLocations"] = ["Paris, France", "Zurich, Switzerland"]
    r = fetch("lever", {("GET", "https://api.lever.co/v0/postings/acme"): httpx.Response(200, json=data)})
    assert [p.title for p in r.postings] == ["Deployment Strategist, Internship"]
    assert r.postings[0].location == "Paris, France; Zurich, Switzerland"


def test_lever_eu_board_uses_eu_api():
    data = fixture_json("lever.json")
    r = fetch("lever", {("GET", "https://api.eu.lever.co/v0/postings/acme"): httpx.Response(200, json=data)},
              site="eu")
    assert r.postings


def test_ashby_skips_unlisted_and_joins_locations():
    data = fixture_json("ashby.json")
    data["jobs"][0]["secondaryLocations"] = [{"location": "Zurich, Switzerland"}]
    r = fetch("ashby", {("GET", "https://api.ashbyhq.com/posting-api/job-board/acme"): httpx.Response(200, json=data)})
    titles = [p.title for p in r.postings]
    assert titles == ["Applied Scientist Intern"]
    assert r.postings[0].location == "New York, NY (HQ); Zurich, Switzerland"


def _workday_routes(page2_status=200):
    lst = fixture_json("workday_list.json")
    intern = lst["jobPostings"][0]
    page2 = {"total": 21, "jobPostings": [dict(intern, title="Praktikum Risk", externalPath="/job/X/Praktikum_R1")]}
    base = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Board"
    pages = iter([httpx.Response(200, json=lst), httpx.Response(page2_status, json=page2)])
    return intern, {("POST", f"{base}/jobs"): lambda req: next(pages),
                    ("GET", f"{base}{intern['externalPath']}"): httpx.Response(200, json=fixture_json("workday_job.json")),
                    ("GET", f"{base}/job/X/Praktikum_R1"): httpx.Response(200, json=fixture_json("workday_job.json"))}


def test_workday_pages_and_fetches_detail():
    intern, routes = _workday_routes()
    r = fetch("workday", routes, board="acme/Board", site="acme.wd5.myworkdayjobs.com")
    assert [p.title for p in r.postings] == [intern["title"], "Praktikum Risk"]
    assert r.postings[0].url == f"https://acme.wd5.myworkdayjobs.com/Board{intern['externalPath']}"
    assert "<p>" not in r.postings[0].description and "DFT focus" in r.postings[0].description


def test_workday_failure_on_a_later_page_is_a_pagination_failure():
    _, routes = _workday_routes(page2_status=500)
    with pytest.raises(SourceFailure) as e:
        fetch("workday", routes, board="acme/Board", site="acme.wd5.myworkdayjobs.com")
    assert e.value.kind == "pagination"


def test_smartrecruiters_reads_details_for_internships_only():
    base = "https://api.smartrecruiters.com/v1/companies/acme/postings"
    detail = []
    routes = {("GET", base): httpx.Response(200, json=fixture_json("smartrecruiters_list.json")),
              ("GET", f"{base}/744000012345678"): lambda req: detail.append(1) or httpx.Response(
                  200, json=fixture_json("smartrecruiters_job.json"))}
    r = fetch("smartrecruiters", routes)
    assert [p.title for p in r.postings] == ["Summer Internship 2027 - Wealth Management (f/m/d)"]
    p = r.postings[0]
    assert p.location == "Zurich; CH" and p.published_at == "2026-10-20"
    assert "graduating in 2028" in p.description and detail == [1]


def test_recruitee_reads_offers():
    r = fetch("recruitee", {("GET", "https://acme.recruitee.com/api/offers/"):
                            httpx.Response(200, json=fixture_json("recruitee.json"))})
    assert [p.title for p in r.postings] == ["Praktikant M&A (m/w/d) 2027"]
    assert r.postings[0].location == "Frankfurt am Main; DE"
    assert "Englischkenntnisse" in r.postings[0].description


def test_personio_reads_xml_feed():
    xml = (FIX / "personio.xml").read_bytes()
    r = fetch("personio", {("GET", "https://acme.jobs.personio.de/xml"): httpx.Response(200, content=xml)})
    assert [p.title for p in r.postings] == ["Intern Private Equity (6 months)"]
    p = r.postings[0]
    assert p.url == "https://acme.jobs.personio.de/job/1523301" and p.location == "Zug"
    assert "Master student" in p.description and r.total_listed == 2


def test_personio_broken_xml_is_invalid():
    with pytest.raises(SourceFailure) as e:
        fetch("personio", {("GET", "https://acme.jobs.personio.de/xml"): httpx.Response(200, content=b"<oops")})
    assert e.value.kind == "invalid"


@pytest.mark.parametrize("status,kind", [(500, "unavailable"), (404, "gone"), (403, "blocked"), (429, "rate_limited")])
def test_http_errors_become_failure_kinds(status, kind):
    with pytest.raises(SourceFailure) as e:
        fetch("lever", {("GET", "https://api.lever.co/v0/postings/acme"): httpx.Response(status)})
    assert e.value.kind == kind


def test_network_error_is_unavailable():
    def boom(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("boom")
    with pytest.raises(SourceFailure) as e:
        connectors.get("lever").fetch(src("lever"), client(transport=httpx.MockTransport(boom)))
    assert e.value.kind == "unavailable"


def test_malformed_json_is_invalid():
    with pytest.raises(SourceFailure) as e:
        fetch("greenhouse", {("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
                             httpx.Response(200, text="<html>nope")})
    assert e.value.kind == "invalid"


def test_unexpected_shape_is_format_changed():
    with pytest.raises(SourceFailure) as e:
        fetch("greenhouse", {("GET", "https://boards-api.greenhouse.io/v1/boards/acme/jobs"):
                             httpx.Response(200, json={"oops": 1})})
    assert e.value.kind == "format_changed"


def test_empty_board_is_a_successful_empty_result():
    r = fetch("ashby", {("GET", "https://api.ashbyhq.com/posting-api/job-board/acme"):
                        httpx.Response(200, json={"jobs": []})})
    assert r.postings == [] and r.complete and r.total_listed == 0


def test_unknown_kind_fails_loudly():
    with pytest.raises(SourceFailure):
        connectors.get("myspace")


@pytest.mark.parametrize("url,kind,board", [
    ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse", "acme"),
    ("https://job-boards.eu.greenhouse.io/acme", "greenhouse", "acme"),
    ("https://jobs.lever.co/acme/abc-123", "lever", "acme"),
    ("https://jobs.ashbyhq.com/Acme/123", "ashby", "Acme"),
    ("https://acme.wd3.myworkdayjobs.com/en-US/External/job/Zurich/Intern_R1", "workday", "acme/External"),
    ("https://jobs.smartrecruiters.com/AcmeBank/7440000", "smartrecruiters", "AcmeBank"),
    ("https://acme.recruitee.com/o/intern", "recruitee", "acme"),
    ("https://acme.jobs.personio.de/job/1", "personio", "acme"),
])
def test_detect_board_urls(url, kind, board):
    ref = connectors.detect(url)
    assert ref is not None and ref.kind == kind and ref.board == board


@pytest.mark.parametrize("url", ["https://www.ubs.com/careers", "https://example.com/jobs", "https://www.recruitee.com/"])
def test_detect_ignores_other_urls(url):
    assert connectors.detect(url) is None


# --- careers pages ----------------------------------------------------------------------------------------------

PAGE_URL = "https://www.examplepartners.ch/careers"


def page_source(**kw):
    return src("page", board=None, url=PAGE_URL, **kw)


def test_page_jsonld_postings_parsed_without_ai_and_boards_found():
    html = (FIX / "careers_jsonld.html").read_text()
    r = CareersPage().fetch(page_source(), serve({("GET", PAGE_URL): httpx.Response(200, text=html)}))
    assert not r.needs_ai
    assert [p.title for p in r.postings] == ["Off-Cycle Intern, M&A Advisory (Geneva)"]
    p = r.postings[0]
    assert p.external_id == "EP-2026-114" and p.url == "https://www.examplepartners.ch/careers/offcycle-ma-geneva"
    assert p.location == "Geneva, CH" and p.published_at == "2026-10-15" and p.extraction == "jsonld"
    assert p.structured["valid_through"] == "2026-12-31"
    assert p.structured["hiring_organization"] == "Example Partners SA"
    assert [s.id for s in r.found_sources] == ["lever:examplepartners"]


def test_page_without_structured_data_needs_ai():
    html = "<html><body>" + "<p>We hire summer interns in Zurich every year. Apply below.</p>" * 10 + "</body></html>"
    r = CareersPage().fetch(page_source(), serve({("GET", PAGE_URL): httpx.Response(200, text=html)}))
    assert r.needs_ai and not r.complete and r.page_text and r.content_hash


def test_page_same_hash_is_not_modified():
    html = "<html><body>" + "<p>No openings at the moment, check back in spring for internships.</p>" * 10
    c = serve({("GET", PAGE_URL): httpx.Response(200, text=html)})
    first = CareersPage().fetch(page_source(), c)
    second = CareersPage().fetch(page_source(content_hash=first.content_hash), c)
    assert second.not_modified and not second.needs_ai


def test_page_304_is_not_modified():
    r = CareersPage().fetch(page_source(etag='"x"'), serve({("GET", PAGE_URL): httpx.Response(304)}))
    assert r.not_modified


def test_source_ref_ids_are_stable():
    assert SourceRef("greenhouse", "https://boards.greenhouse.io/acme", "acme").id == "greenhouse:acme"
    a, b = SourceRef("page", "https://a.example/jobs"), SourceRef("page", "https://a.example/jobs")
    assert a.id == b.id and a.id.startswith("page:")
    assert json.dumps(a.id)
