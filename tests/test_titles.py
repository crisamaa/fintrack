import pytest

from tracker.titles import looks_like_internship


@pytest.mark.parametrize("t", [
    "Summer Analyst 2027", "Praktikum Risk (m/w/d)", "Stage M&A",
    "Stagiaire Asset Management", "Tirocinio Corporate Finance", "Prácticas Banca",
    "Stagiair Finance", "Werkstudent Controlling", "Off-Cycle Intern",
    "Trainee Wealth Management", "Praktikant Treasury",
])
def test_matches(t):
    assert looks_like_internship(t)


@pytest.mark.parametrize("t", [
    "Internal Auditor", "International Sales Director", "Stage Manager", "VP Internal Controls",
])
def test_rejects(t):
    assert not looks_like_internship(t)


@pytest.mark.parametrize("title", ["Working Student Risk (m/w/d)", "Practicante de Trading 2027", "Becaria Finanzas",
                                   "Spring Week 2027", "Insight Week Investment Banking"])
def test_v2_multilingual_titles(title):
    assert looks_like_internship(title)
