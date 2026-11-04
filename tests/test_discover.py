from pathlib import Path

import httpx
import pytest
import respx
import yaml

from tracker import discover as disc
from tracker.discover import discover, find_finma_pdf_url, name_similarity, parse_finma_names, slug_candidates
from tracker.feeds import SourceError

FIXTURE = Path(__file__).parent / "fixtures" / "finma_sample.pdf"
PAGE = disc.FINMA_PAGE
PDF_URL = "https://www.finma.ch/en/~/media/finma/dokumente/bewilligungstraeger/pdf/beh.pdf?sc_lang=en&hash=AB12"
PAGE_HTML = f"""<html><body>
<a href="/en/~/media/finma/dokumente/bewilligungstraeger/xlsx/beh.xlsx?sc_lang=en&amp;hash=1">List of banks and securities firms authorised by FINMA (incl. those in discontinuation)</a>
<a href="/en/~/media/finma/dokumente/bewilligungstraeger/pdf/raiff.pdf?sc_lang=en&amp;hash=2">List of Raiffeisen banks authorised by FINMA</a>
<a href="/de/~/media/finma/dokumente/bewilligungstraeger/pdf/beh.pdf?la=de&amp;hash=3">DE</a>
<a href="/en/~/media/finma/dokumente/bewilligungstraeger/pdf/beh.pdf?sc_lang=en&amp;hash=AB12">List of banks and securities firms authorised by FINMA (incl. those in discontinuation of business)</a>
</body></html>"""

NAMES = ["Aargauische Kantonalbank", "Alpian SA", "Bank Julius Bär & Co. AG",
         "Bank of America Europe Designated Activity Company", "Bank Thalwil Genossenschaft",
         "Deutsche Bank (Suisse) SA", "Crypto Finance AG", "Zürcher Kantonalbank"]


def test_slug_candidates():
    c = slug_candidates("Bank Julius Bär & Co. AG")
    assert "juliusbaer" in c and "julius-baer" in c
    assert len(c) == len(set(c)) and len(c) <= disc.MAX_SLUGS


def test_slug_candidates_ascii_lowercase():
    assert all(s == s.lower() and s.isascii() for s in slug_candidates("Zürcher Kantonalbank (Schweiz) AG"))


def test_parse_finma_names():
    names = parse_finma_names(FIXTURE.read_bytes())
    assert names == NAMES


def test_parse_finma_names_rejects_non_pdf():
    with pytest.raises(SourceError):
        parse_finma_names(b"<html>not a pdf</html>")


def test_find_finma_pdf_url_picks_the_english_banks_list():
    assert find_finma_pdf_url(PAGE_HTML, PAGE) == PDF_URL


def test_find_finma_pdf_url_missing_raises():
    with pytest.raises(SourceError):
        find_finma_pdf_url("<a href='/x.pdf'>Something else</a>", PAGE)


def test_similarity_accepts_same_firm_and_rejects_collisions():
    assert name_similarity("Alpian SA", "Alpian") >= disc.SIMILARITY_THRESHOLD
    assert name_similarity("Bank Julius Bär & Co. AG", "Julius Baer") >= disc.SIMILARITY_THRESHOLD
    assert name_similarity("Bank Thalwil Genossenschaft", "Thalwil Plumbing Supplies") < disc.SIMILARITY_THRESHOLD
    assert name_similarity("Alpian SA", "Alpine Labs") < disc.SIMILARITY_THRESHOLD
    assert name_similarity("Bank Avera Genossenschaft", "Avera Health") < disc.SIMILARITY_THRESHOLD


GH = "https://boards-api.greenhouse.io/v1/boards"
LEVER = "https://api.lever.co/v0/postings"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board"


@pytest.fixture
def files(tmp_path):
    firms = tmp_path / "firms.yaml"
    firms.write_text(yaml.safe_dump([
        {"id": "deutsche-bank", "name": "Deutsche Bank", "sector": "banking", "country": "DE", "source": "page",
         "careers_url": "https://careers.db.com/"}]))
    return firms, tmp_path / "unresolved.yaml"


def mock_finma(router, pdf=None):
    router.get(PAGE).respond(200, text=PAGE_HTML)
    router.get(PDF_URL).respond(200, content=FIXTURE.read_bytes() if pdf is None else pdf)


def mock_no_boards(router):
    router.get(url__startswith=GH).respond(404)
    router.get(url__startswith=LEVER).respond(404)
    router.get(url__startswith=ASHBY).respond(404)
    router.get(url__startswith="https://jobs.lever.co").respond(404)
    router.get(url__startswith="https://jobs.ashbyhq.com").respond(404)


def mock_alpian_board(router, board_name="Alpian"):
    router.get(f"{GH}/alpian").respond(200, json={"name": board_name, "content": ""})
    router.get(f"{GH}/alpian/jobs").respond(200, json={"jobs": []})


def load(path):
    return yaml.safe_load(path.read_text())


def test_discover_adds_match_once(files):
    firms, unresolved = files
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        mock_alpian_board(r)
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            assert discover(firms, unresolved, client) == 1
            added = [f for f in load(firms) if f["id"] != "deutsche-bank"]
            assert added == [{"id": "alpian", "name": "Alpian SA", "sector": "banking", "country": "CH",
                              "source": "greenhouse", "slug": "alpian"}]
            assert discover(firms, unresolved, client) == 0
    assert len(load(firms)) == 2


def test_discover_ignores_a_board_whose_name_does_not_resemble(files):
    firms, unresolved = files
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        mock_alpian_board(r, board_name="Alpine Labs")
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            assert discover(firms, unresolved, client) == 0
    assert "Alpian SA" in load(unresolved)
    assert len(load(firms)) == 1


def test_discover_matches_lever_and_ashby_by_page_title(files):
    firms, unresolved = files
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        r.get(f"{LEVER}/cryptofinance").respond(200, json=[])
        r.get("https://jobs.lever.co/cryptofinance").respond(200, text="<html><title>Crypto Finance</title></html>")
        r.get(f"{ASHBY}/aargauischekantonalbank").respond(200, json={"jobs": []})
        r.get("https://jobs.ashbyhq.com/aargauischekantonalbank").respond(
            200, text="<html><title>Aargauische Kantonalbank Jobs</title></html>")
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            assert discover(firms, unresolved, client) == 2
    added = {f["id"]: f for f in load(firms)}
    assert added["crypto-finance"]["source"] == "lever" and added["crypto-finance"]["slug"] == "cryptofinance"
    assert added["aargauische-kantonalbank"]["source"] == "ashby"


def test_discover_records_unmatched(files):
    firms, unresolved = files
    before = firms.read_text()
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            assert discover(firms, unresolved, client) == 0
            assert firms.read_text() == before
            names = load(unresolved)
            assert "Alpian SA" in names and "Zürcher Kantonalbank" in names
            assert "Deutsche Bank (Suisse) SA" not in names  # already covered by an existing firm
            assert len(names) == len(set(names))
            discover(firms, unresolved, client)
    assert load(unresolved) == names  # no duplicates after a second run


def test_existing_firm_not_duplicated(files):
    firms, unresolved = files
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        r.get(f"{GH}/deutschebank").respond(200, json={"name": "Deutsche Bank", "content": ""})
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            discover(firms, unresolved, client)
        assert not any(c.request.url.path.endswith("deutschebank") for c in r.calls)
    assert [f["id"] for f in load(firms)].count("deutsche-bank") == 1
    assert len(load(firms)) == 1


def test_failure_raises_and_changes_nothing(files):
    firms, unresolved = files
    before = firms.read_text()
    with respx.mock(assert_all_called=False) as r:
        r.get(PAGE).respond(200, text=PAGE_HTML)
        r.get(PDF_URL).respond(500)
        with httpx.Client(follow_redirects=True) as client:
            with pytest.raises(SourceError):
                discover(firms, unresolved, client)
    assert firms.read_text() == before and not unresolved.exists()


def test_page_without_the_list_raises(files):
    firms, unresolved = files
    with respx.mock() as r:
        r.get(PAGE).respond(200, text="<html>nothing here</html>")
        with httpx.Client() as client:
            with pytest.raises(SourceError):
                discover(firms, unresolved, client)
    assert not unresolved.exists()


def test_unreadable_pdf_raises(files):
    firms, unresolved = files
    with respx.mock() as r:
        mock_finma(r, pdf=b"%PDF-1.7 garbage")
        with httpx.Client() as client:
            with pytest.raises(SourceError):
                discover(firms, unresolved, client)
    assert not unresolved.exists()


def test_probe_cap_is_stable_and_leaves_the_rest_for_next_month(files, monkeypatch):
    firms, unresolved = files
    monkeypatch.setattr(disc, "MAX_PROBE_FIRMS", 3)
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            discover(firms, unresolved, client)
            first = load(unresolved)
            assert len(first) == 3
            discover(firms, unresolved, client)  # next month: never-probed names go first
            second = load(unresolved)
            assert len(second) == 6 and set(first) < set(second)
            discover(firms, unresolved, client)
    assert len(load(unresolved)) == 7  # 8 names minus the one covered by an existing firm


def test_transient_error_is_not_recorded_as_unresolved(files):
    firms, unresolved = files
    with respx.mock(assert_all_called=False) as r:
        mock_finma(r)
        r.get(f"{GH}/alpian").respond(503)
        mock_no_boards(r)
        with httpx.Client(follow_redirects=True) as client:
            discover(firms, unresolved, client)
    assert "Alpian SA" not in load(unresolved)


def test_main_runs_discover_with_a_polite_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake(firms_path, unresolved_path, client):
        seen.update(f=firms_path, u=unresolved_path, ua=client.headers["User-Agent"], hooks=client.event_hooks)
        return 4

    monkeypatch.setattr(disc, "discover", fake)
    disc.main()
    assert seen["f"] == Path("firms/firms.yaml") and seen["u"] == Path("firms/unresolved.yaml")
    assert seen["ua"] == "europe-internship-tracker/1.0"
    assert seen["hooks"]["request"] and seen["hooks"]["response"]
