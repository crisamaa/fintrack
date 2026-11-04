import json
from pathlib import Path

import httpx
import pytest
import respx

from tracker.feeds import SourceError, fetch_feed
from tracker.models import Firm

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


def firm(source, slug="acme", site=None):
    return Firm(id="acme", name="Acme", sector="fintech", country="CH",
                source=source, slug=slug, site=site)


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


@respx.mock
def test_greenhouse_keeps_only_internships(client):
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    route = respx.get(url, params={"content": "true"}).respond(json=load("greenhouse"))
    postings = fetch_feed(firm("greenhouse"), client)
    assert route.called
    assert [p.title for p in postings] == ["Data Analyst, Intern"]
    p = postings[0]
    assert p.firm_id == "acme"
    assert p.url.startswith("https://")
    assert p.location == "Toronto"
    assert "<" not in p.description and "Who we are" in p.description


@respx.mock
def test_lever_keeps_only_internships(client):
    respx.get("https://api.lever.co/v0/postings/acme", params={"mode": "json"}).respond(json=load("lever"))
    postings = fetch_feed(firm("lever"), client)
    assert [p.title for p in postings] == ["Deployment Strategist, Internship"]
    assert postings[0].url.startswith("https://jobs.lever.co/")
    assert postings[0].location == "Paris, France"
    assert postings[0].description


@respx.mock
def test_lever_joins_multiple_locations(client):
    data = load("lever")
    data[0]["categories"]["allLocations"] = ["Paris, France", "Zurich, Switzerland"]
    respx.get("https://api.lever.co/v0/postings/acme").respond(json=data)
    assert fetch_feed(firm("lever"), client)[0].location == "Paris, France; Zurich, Switzerland"


@respx.mock
def test_ashby_keeps_internships_and_joins_locations(client):
    data = load("ashby")
    data["jobs"][0]["secondaryLocations"] = [{"location": "Zurich, Switzerland"}]
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(json=data)
    postings = fetch_feed(firm("ashby"), client)
    assert [p.title for p in postings] == ["Applied Scientist Intern"]
    assert postings[0].location == "New York, NY (HQ); Zurich, Switzerland"
    assert postings[0].url.startswith("https://")


@respx.mock
def test_ashby_skips_unlisted(client):
    data = load("ashby")
    assert any(j["isListed"] is False and "Intern" in j["title"] for j in data["jobs"])
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(json=data)
    titles = [p.title for p in fetch_feed(firm("ashby"), client)]
    assert "Software Engineering Intern (unlisted)" not in titles


@respx.mock
def test_workday_pages_and_fetches_detail(client):
    lst = load("workday_list")  # total=21, one internship and one other
    intern, other = lst["jobPostings"]
    page2 = {"total": 21, "jobPostings": [dict(intern, title="Praktikum Risk", externalPath="/job/X/Praktikum_R1")]}
    base = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Board"
    list_route = respx.post(f"{base}/jobs")
    list_route.side_effect = [httpx.Response(200, json=lst), httpx.Response(200, json=page2)]
    respx.get(f"{base}{intern['externalPath']}").respond(json=load("workday_job"))
    respx.get(f"{base}/job/X/Praktikum_R1").respond(json=load("workday_job"))
    postings = fetch_feed(firm("workday", slug="acme/Board", site="acme.wd5.myworkdayjobs.com"), client)
    bodies = [json.loads(c.request.content) for c in list_route.calls]
    assert [b["offset"] for b in bodies] == [0, 20]
    assert all(b["limit"] == 20 and b["appliedFacets"] == {} for b in bodies)
    assert [p.title for p in postings] == [intern["title"], "Praktikum Risk"]
    assert postings[0].url == f"https://acme.wd5.myworkdayjobs.com/Board{intern['externalPath']}"
    assert postings[0].location == intern["locationsText"]
    assert "<p>" not in postings[0].description and "DFT focus" in postings[0].description


@respx.mock
def test_http_500_raises_source_error(client):
    respx.get("https://api.lever.co/v0/postings/acme").respond(500)
    with pytest.raises(SourceError):
        fetch_feed(firm("lever"), client)


@respx.mock
def test_network_error_raises_source_error(client):
    respx.get("https://api.lever.co/v0/postings/acme").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(SourceError):
        fetch_feed(firm("lever"), client)


@respx.mock
def test_malformed_json_raises_source_error(client):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(text="<html>nope")
    with pytest.raises(SourceError):
        fetch_feed(firm("greenhouse"), client)


@respx.mock
def test_unexpected_shape_raises_source_error(client):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").respond(json={"oops": 1})
    with pytest.raises(SourceError):
        fetch_feed(firm("greenhouse"), client)


def test_unsupported_source_raises_source_error(client):
    with pytest.raises(SourceError):
        fetch_feed(firm("page"), client)
