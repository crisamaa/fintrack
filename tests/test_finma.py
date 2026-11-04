from pathlib import Path

import pytest

from tracker import discovery as disc
from tracker.discovery import find_finma_pdf_url, parse_finma_names
from tracker.http import SourceFailure as SourceError
from tracker.work import slug_candidates

FIXTURE = Path(__file__).parent / "fixtures" / "finma_sample.pdf"
PAGE = disc.FINMA_PAGE
PDF_URL = "https://www.finma.ch/en/~/media/finma/dokumente/bewilligungstraeger/pdf/beh.pdf?sc_lang=en&hash=AB12"
PAGE_HTML = """<html><body>
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
    assert len(c) == len(set(c)) and len(c) <= 5


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


