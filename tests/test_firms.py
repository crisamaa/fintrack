import re
from collections import Counter
from pathlib import Path

import pytest

from tracker.firms import load_firms, make_id, name_key, same_firm
from tracker.models import ALLOWED_COUNTRIES

COMMITTED = Path(__file__).parent.parent / "firms" / "firms.yaml"
MIN_FIRMS = 150


def write(tmp_path, text):
    p = tmp_path / "firms.yaml"
    p.write_text(text)
    return p


GOOD = "{id: acme, name: Acme, sector: banking, country: CH, source: greenhouse, slug: acme}"


def test_committed_firm_list_is_valid():
    firms = load_firms(COMMITTED)
    assert len(firms) >= MIN_FIRMS
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", f.id) for f in firms)
    assert all(f.country in ALLOWED_COUNTRIES for f in firms)


def test_committed_list_is_weighted_toward_switzerland():
    by_country = Counter(f.country for f in load_firms(COMMITTED))
    assert by_country["CH"] == max(by_country.values())
    assert len(by_country) >= 10


def test_committed_list_sources_have_what_the_reader_needs():
    for f in load_firms(COMMITTED):
        if f.source == "page":
            assert f.careers_url and f.careers_url.startswith("https://"), f.id
        else:
            assert f.slug, f.id
        if f.source == "workday":
            assert f.site and "/" in f.slug, f.id


def test_loads_plain_list_and_firms_mapping(tmp_path):
    assert [f.id for f in load_firms(write(tmp_path, f"- {GOOD}\n"))] == ["acme"]
    assert [f.id for f in load_firms(write(tmp_path, f"firms:\n  - {GOOD}\n"))] == ["acme"]


def test_duplicate_id_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        load_firms(write(tmp_path, f"- {GOOD}\n- {GOOD}\n"))


def test_unknown_sector_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_firms(write(tmp_path, "- {id: a, name: A, sector: crypto, country: CH, source: greenhouse, slug: a}\n"))


def test_country_outside_allowed_rejected(tmp_path):
    with pytest.raises(ValueError, match="country"):
        load_firms(write(tmp_path, "- {id: a, name: A, sector: banking, country: US, source: greenhouse, slug: a}\n"))


def test_id_must_be_lowercase_slug(tmp_path):
    with pytest.raises(ValueError, match="id"):
        load_firms(write(tmp_path, "- {id: Acme Bank, name: A, sector: banking, country: CH, source: page, careers_url: 'https://a.ch'}\n"))


def test_source_needs_its_fields(tmp_path):
    with pytest.raises(ValueError, match="slug"):
        load_firms(write(tmp_path, "- {id: a, name: A, sector: banking, country: CH, source: lever}\n"))
    with pytest.raises(ValueError, match="careers_url"):
        load_firms(write(tmp_path, "- {id: a, name: A, sector: banking, country: CH, source: page}\n"))
    with pytest.raises(ValueError, match="workday"):
        load_firms(write(tmp_path, "- {id: a, name: A, sector: banking, country: CH, source: workday, slug: noboard, site: a.wd3.myworkdayjobs.com}\n"))


def test_empty_or_malformed_file_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_firms(write(tmp_path, "just: a mapping\n"))


@pytest.mark.parametrize("a,b", [
    ("Julius Baer", "Bank Julius Bär & Co. AG"),
    ("Vontobel", "Bank Vontobel AG"),
    ("Deutsche Bank", "Deutsche Bank (Suisse) SA"),
    ("Bank of America", "Bank of America Europe Designated Activity Company"),
])
def test_same_firm_matches_variants(a, b):
    assert same_firm(a, b) and same_firm(b, a)


@pytest.mark.parametrize("a,b", [("Julius Baer", "Bank J. Safra Sarasin AG"), ("UBS", "Bank Vontobel AG"),
                                 ("Credit Agricole", "Credit Mutuel de la Vallee SA")])
def test_same_firm_rejects_different_firms(a, b):
    assert not same_firm(a, b)


def test_name_key_reads_dotted_legal_forms():
    assert name_key("ABANCA CORPORACION BANCARIA S.A.") == name_key("Abanca Corporacion Bancaria SA")
    assert name_key("Bank J. Safra Sarasin AG") == "jsafrasarasin"


def test_name_key_folds_accents_and_legal_forms():
    assert name_key("Bank Julius Bär & Co. AG") == name_key("Julius Baer")


@pytest.mark.parametrize("name,expected", [
    ("Bank Julius Bär & Co. AG", "bank-julius-baer"),
    ("J.P. Morgan", "jp-morgan"),
    ("Bank of America Europe Designated Activity Company, Dublin, Zweigniederlassung Zürich",
     "bank-of-america-europe-designated-activity"),
    ("Zürcher Kantonalbank", "zuercher-kantonalbank"),
])
def test_make_id_is_a_stable_lowercase_slug(name, expected):
    assert make_id(name) == expected
