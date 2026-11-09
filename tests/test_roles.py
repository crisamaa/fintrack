"""Role-level classification: what the intern will do, independent of the employer's sector."""
import pytest

from tracker import roles


@pytest.mark.parametrize("title, function", [
    # Finance functions, from titles seen in the live data.
    ("2028 Summer Analyst Intern | M&A", "m_and_a"),
    ("Buyout Intern - February 2027- Frankfurt (M/F)", "private_equity"),
    ("Copenhagen - Private Equity Internship 2027", "private_equity"),
    ("Private Equity Real Estate Stage - Mars 2027 I Paris (M/F)", "private_equity"),
    ("Summer Analyst Programme 2027 – EQT Real Estate", "real_estate"),
    ("Sales, Trading and Structuring Off Cycle Internship Programme 2027 London", "sales_trading"),
    ("Quantitative Trader Intern - Summer 2027", "quant"),
    ("INTERNSHIP - CREDIT RESEARCH SELL SIDE - PARIS", "research"),
    ("Private Credit Intern - December 2026 | London", "credit"),
    ("3 Month Internship  - Financial Restructuring (Q3 2027)", "restructuring"),
    ("Capital Markets Off Cycle Internship Programme 2027 Frankfurt", "capital_markets"),
    ("Debt Advisory Internship (March 2027)", "dcm"),
    ("Private Bank and Wealth Management Summer Internship Programme 2027 Jersey", "private_banking"),
    ("UK Corporate Banking Summer Internship Programme 2027 London", "banking"),
    ("Group Finance Treasury Stage - Mars 2027 I Paris (H/F)", "treasury"),
    ("Stage Analyste risques de marché - contrepartie", "risk"),
    ("Fund Finance Secondaries & Primaries Intern - February 2027 I Zurich (M/F)", "finance_control"),
    ("Corporate Development (Stratégie - M&A) Stage - Mars 2027  | Paris (H/F)", "m_and_a"),
    ("Q2 2027 Off-Cycle Internship - Corporate Finance (IT Services)", "corporate_finance"),
    ("Werkstudent Portfolio Management Systems – Product Management (m/w/d)", "product"),
    # Non-finance functions at financial employers.
    ("Software Engineer Internship (6 months from feb/march 2027)", "technology"),
    ("2027 Technology Developer Summer Internship Programme London", "technology"),
    ("Internship – Software Engineer – Global Markets Services AI Project", "technology"),
    ("Secondaries & Primaries IT Project Management Intern - September 2026 | London", "technology"),
    ("Data Scientist Intern - February 2027 | NYC", "data"),
    ("2027 Human Resources Analyst Summer Internship Programme Glasgow", "hr"),
    ("Werkstudent Talent Acquisition – Recruiting & Employer Branding (m/w/d)", "hr"),
    ("Investor Relations Intern - January 2027 I Zurich", "investor_relations"),
    ("2027 Compliance Analyst Summer Internship Programme London", "compliance_legal"),
    ("Legal Intern - July 2027 | Luxembourg (M/F)", "compliance_legal"),
    ("2027 Operations Analyst Summer Internship Programme London", "operations"),
    ("Regional Marketing Intern - Italy (curricular internship)", "marketing"),
    ("Business Developer - Paris - Stage F/H", "sales"),
    ("ESG internship | London | 6-months", "sustainability"),
    ("STAGE - Assistant(e) de Gestion", "wealth_management"),
    ("Werkstudent (m/w/d) Privatkundenbetreuung", "wealth_management"),
    ("Payments and Reconciliations Intern (f/m/d)", "operations"),
    ("NextGen Tech Internship Program", "technology"),
])
def test_title_rules(title, function):
    r = roles.classify(title)
    assert r["function"] == function, r
    assert r["method"] == "title"
    assert r["confidence"] in ("high", "medium")
    assert r["evidence"]


def test_engineering_role_at_finance_firm_is_not_finance():
    r = roles.classify("Internship – AI Software Engineer - AI-Powered Relationship Intelligence for Private Banking")
    assert r["function"] == "technology"
    assert roles.FUNCTIONS[r["function"]]["finance"] is False
    assert "private_banking" in r["functions"]  # the domain is kept as a secondary function


def test_multiple_functions_are_kept_in_order():
    r = roles.classify("2027 Quantitative Masters Internship Program - Investments - Portfolio Management - New York")
    assert r["function"] in ("quant", "asset_management")
    assert {"quant", "asset_management"} <= set(r["functions"])


def test_generic_titles_are_unknown_not_guessed():
    for t in ("Stage", "Summer Internship", "Internships for Students", "Graduate Programme", "Trainee-Programm",
              "Internship Frankfurt - H1 2027"):
        r = roles.classify(t)
        assert r["function"] == "unknown", (t, r)
        assert r["confidence"] == "none"


def test_generic_investment_title_is_medium_confidence_investing():
    r = roles.classify("Investment Intern")
    assert r["function"] == "investing"
    assert r["confidence"] == "medium"


def test_department_fills_a_generic_title():
    r = roles.classify("Summer Internship", department="Mergers & Acquisitions")
    assert r["function"] == "m_and_a"
    assert r["method"] == "department"
    assert r["confidence"] == "medium"


def test_description_is_low_confidence_evidence_only():
    desc = ("You will join our private equity team, support the private equity deal team on due diligence and "
            "screen new private equity opportunities.")
    r = roles.classify("Summer Internship", description=desc)
    assert r["function"] == "private_equity"
    assert (r["method"], r["confidence"]) == ("description", "low")
    one_mention = roles.classify("Summer Internship", description="Our private equity firm hires interns.")
    assert one_mention["function"] == "unknown"


def test_sales_and_trading_is_not_generic_sales():
    assert roles.classify("Sales & Trading Summer Analyst")["function"] == "sales_trading"
    assert roles.classify("Early Career Intern - Institutional Sales (Retirement & Insurance)")["function"] == "sales"


def test_it_services_coverage_is_not_a_technology_role():
    r = roles.classify("Q2 2027 Off-Cycle Internship - Corporate Finance (IT Services)")
    assert "technology" not in r["functions"]


def test_every_function_has_a_family_and_label():
    for fid, f in roles.FUNCTIONS.items():
        assert f["label"] and f["family"] in roles.FAMILIES, fid
        assert isinstance(f["finance"], bool)
    assert "unknown" not in roles.FUNCTIONS


def test_taxonomy_export_shape():
    t = roles.taxonomy()
    assert t["version"] == roles.VERSION
    assert {f["id"] for f in t["functions"]} == set(roles.FUNCTIONS)
    assert set(t["families"]) == set(roles.FAMILIES)


# --- persistence, AI layer and export ----------------------------------------------------------------------------
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from tests.conftest import FakeProvider, make_ctx  # noqa: E402
from tests.helpers import employer, later, opp_for, posting, result, row, source  # noqa: E402
from tracker import export, ingest, work  # noqa: E402
from tracker.db import one  # noqa: E402


def _role(ctx, opp_id):
    return dict(one(ctx.conn, "SELECT * FROM opportunity_roles WHERE opportunity_id = ?", (opp_id,)))


def _ingest(ctx, *postings):
    ingest.ingest(ctx, row(ctx, "greenhouse:acme"), result(*postings))


def test_ingest_classifies_with_department_and_is_idempotent(ctx):
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1, title="Summer Internship 2027", structured={"department": "Leveraged Finance"}))
    o = opp_for(ctx, 1)
    r = _role(ctx, o["id"])
    assert (r["function"], r["method"], r["version"]) == ("dcm", "department", roles.VERSION)
    assert roles.refresh(ctx.conn, ctx.now) == 0  # unchanged text and version: nothing recomputed


def test_changed_title_reclassifies_and_records_history(ctx):
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1, title="2027 Summer Analyst - Technology Developer"))
    o = opp_for(ctx, 1)
    assert _role(ctx, o["id"])["function"] == "technology"
    later(ctx, hours=8)
    _ingest(ctx, posting(1, title="2027 Summer Analyst - M&A Advisory"))
    assert _role(ctx, o["id"])["function"] == "m_and_a"
    ev = one(ctx.conn, "SELECT * FROM opportunity_events WHERE opportunity_id = ? AND field = 'role'", (o["id"],))
    assert (ev["old_value"], ev["new_value"]) == ("technology", "m_and_a")


def test_version_bump_backfills_every_opportunity(ctx, monkeypatch):
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1), posting(2, title="Legal Intern"))
    ctx.conn.execute("DELETE FROM opportunity_roles")
    assert roles.refresh(ctx.conn, ctx.now) == 2
    monkeypatch.setattr(roles, "VERSION", "roles-test")
    assert roles.refresh(ctx.conn, ctx.now) == 2
    assert {r[0] for r in ctx.conn.execute("SELECT version FROM opportunity_roles")} == {"roles-test"}


def _enrich(role_function, role_quote, title="Intern"):
    reply = json.dumps({"is_internship": True, "countries": ["CH"], "city": "Zurich", "internship_type": "summer",
                        "intake_year": 2027, "closes_on": None, "closes_quote": None, "languages": [],
                        "requirements": [], "role_function": role_function, "role_quote": role_quote})
    ctx = make_ctx(provider=FakeProvider(default=reply))
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1, title=title, location="", description="You will support the deal team on buyouts."))
    work.drain(ctx, ["enrich"])
    return ctx, _role(ctx, opp_for(ctx, 1)["id"])


def test_ai_fills_an_unknown_role_only_with_a_verbatim_quote():
    _, r = _enrich("private_equity", "support the deal team on buyouts")
    assert (r["function"], r["method"], r["confidence"]) == ("private_equity", "ai", "medium")
    _, r = _enrich("private_equity", "a quote that is not in the text")
    assert r["function"] == "unknown"
    _, r = _enrich("astrology", "support the deal team on buyouts")
    assert r["function"] == "unknown"


def test_ai_never_overrides_a_confident_rule():
    _, r = _enrich("private_equity", "support the deal team on buyouts", title="Software Engineer Intern")
    assert (r["function"], r["method"]) == ("technology", "title")


def test_export_carries_role_taxonomy_and_program_roles(ctx):
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1, title="Buyout Intern 2027"))
    data = export.opportunities(ctx)
    assert data["role_taxonomy"]["version"] == roles.VERSION
    o = data["opportunities"][0]
    assert o["role"]["function"] == "private_equity" and o["role"]["confidence"] == "high"


def test_site_taxonomy_copy_matches_the_tracker():
    """site/match.js keeps a fallback copy of the function taxonomy; it must not drift."""
    js = (Path(__file__).parent.parent / "site" / "match.js").read_text()
    block = js[js.index("export const FUNCTIONS"):js.index("};", js.index("export const FUNCTIONS"))]
    found = dict(re.findall(r"(\w+): \{ label: \"[^\"]+\", family: \"(\w+)\"", block))
    assert found == {k: v["family"] for k, v in roles.FUNCTIONS.items()}


@pytest.mark.parametrize("title, function", [
    # A coverage sector after a dash names the industry covered, not the work.
    ("Investment Banking Summer Analyst - Technology, Media & Telecom", "investment_banking"),
    ("M&A Intern - Technology Sector", "m_and_a"),
    ("Private Equity Intern - Healthcare & Technology", "private_equity"),
    ("Equity Research Intern - Technology", "research"),
    ("Venture Capital Intern - AI & Software", "venture_capital"),
    ("Private Banking Intern - Client Relationship Management", "private_banking"),
    ("Quantitative Analytics Associate Off Cycle Internship 2027 Paris", "quant"),
    ("Quantitative Intern - Portfolio Engineering and Analytics", "quant"),
    ("Quantitative Research Intern – AI for Investment Research", "quant"),
    ("Fixed Income Sales Summer Analyst", "sales_trading"),
    ("Equity Sales Intern", "sales_trading"),
    # ...while a person's job stays what it is.
    ("Internship – Software Engineer – Global Markets Services AI Project", "technology"),
    ("Werkstudent Portfolio Management Systems – Product Management (m/w/d)", "product"),
])
def test_coverage_suffixes_and_desk_sales(title, function):
    assert roles.classify(title)["function"] == function


def test_a_source_without_department_keeps_the_stored_one(ctx):
    employer(ctx)
    source(ctx)
    _ingest(ctx, posting(1, title="Summer Internship 2027", structured={"department": "Leveraged Finance"}))
    o = opp_for(ctx, 1)
    later(ctx, hours=8)
    _ingest(ctx, posting(1, title="Summer Internship 2027", description="Changed text so the posting is re-read."))
    assert _role(ctx, o["id"])["function"] == "dcm"
