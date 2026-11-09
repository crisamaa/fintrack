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
