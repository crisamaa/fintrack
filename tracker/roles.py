"""Role-level classification: what an internship involves, independent of the employer's industry.

A bank's software internship is technology, a private-equity firm's investor-relations internship is investor
relations. The classifier is layered and deterministic; each layer only runs when the one before found nothing:

  1. the job title (high confidence for a specific function, medium for a generic one such as "Investment Intern")
  2. the department the job board states (medium)
  3. repeated finance terms in the description (low; one mention is not evidence)
  4. Gemini, during enrichment, only when the rules end at low or nothing, and only with a verbatim quote (medium)
  5. otherwise "unknown" — never guessed from the employer's sector.

Results are stored in `opportunity_roles` with the classifier version and a hash of the inputs, so an opportunity is
classified once per material version of its text and reused by every career target in the browser.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime

from tracker.db import dumps, iso, loads, one
from tracker.taxonomy import fold

VERSION = "roles-2"

FAMILIES: dict[str, str] = {
    "advisory": "Advisory & capital markets",
    "markets": "Markets",
    "investing": "Investing",
    "wealth": "Wealth & private banking",
    "banking": "Banking",
    "corporate": "Corporate & risk finance",
    "business": "Business functions",
    "technology": "Technology & data",
}

# id -> label, family, and whether the function is a finance function. Finance ids reuse taxonomy.SECTORS keys
# where one exists, so the site's existing sector vocabulary still applies.
FUNCTIONS: dict[str, dict] = {
    "investment_banking": {"label": "Investment banking", "family": "advisory", "finance": True},
    "m_and_a": {"label": "M&A advisory", "family": "advisory", "finance": True},
    "capital_markets": {"label": "Capital markets", "family": "advisory", "finance": True},
    "ecm": {"label": "Equity capital markets", "family": "advisory", "finance": True},
    "dcm": {"label": "Debt capital markets & debt advisory", "family": "advisory", "finance": True},
    "restructuring": {"label": "Restructuring", "family": "advisory", "finance": True},
    "corporate_finance": {"label": "Corporate finance & valuation", "family": "advisory", "finance": True},
    "sales_trading": {"label": "Sales & trading", "family": "markets", "finance": True},
    "research": {"label": "Equity & credit research", "family": "markets", "finance": True},
    "quant": {"label": "Quantitative finance", "family": "markets", "finance": True},
    "investing": {"label": "Investing (team not stated)", "family": "investing", "finance": True},
    "private_equity": {"label": "Private equity", "family": "investing", "finance": True},
    "venture_capital": {"label": "Venture capital", "family": "investing", "finance": True},
    "hedge_funds": {"label": "Hedge funds", "family": "investing", "finance": True},
    "alternatives": {"label": "Alternatives & infrastructure", "family": "investing", "finance": True},
    "real_estate": {"label": "Real estate investing", "family": "investing", "finance": True},
    "credit": {"label": "Credit & lending", "family": "investing", "finance": True},
    "asset_management": {"label": "Asset management", "family": "investing", "finance": True},
    "family_office": {"label": "Family office", "family": "investing", "finance": True},
    "wealth_management": {"label": "Wealth management", "family": "wealth", "finance": True},
    "private_banking": {"label": "Private banking", "family": "wealth", "finance": True},
    "banking": {"label": "Corporate & retail banking", "family": "banking", "finance": True},
    "treasury": {"label": "Treasury", "family": "corporate", "finance": True},
    "risk": {"label": "Financial risk", "family": "corporate", "finance": True},
    "finance_control": {"label": "Finance & controlling", "family": "corporate", "finance": True},
    "investment_consulting": {"label": "Investment consulting", "family": "corporate", "finance": True},
    "fintech": {"label": "Fintech & financial strategy", "family": "corporate", "finance": True},
    "other_finance": {"label": "Other finance", "family": "corporate", "finance": True},
    "investor_relations": {"label": "Investor relations & fundraising", "family": "business", "finance": False},
    "strategy": {"label": "Strategy & business management", "family": "business", "finance": False},
    "sustainability": {"label": "Sustainability & ESG", "family": "business", "finance": False},
    "sales": {"label": "Sales & business development", "family": "business", "finance": False},
    "operations": {"label": "Operations", "family": "business", "finance": False},
    "compliance_legal": {"label": "Compliance & legal", "family": "business", "finance": False},
    "hr": {"label": "HR & recruiting", "family": "business", "finance": False},
    "marketing": {"label": "Marketing & communications", "family": "business", "finance": False},
    "product": {"label": "Product", "family": "technology", "finance": False},
    "data": {"label": "Data & analytics", "family": "technology", "finance": False},
    "technology": {"label": "Technology & engineering", "family": "technology", "finance": False},
}
# Generic functions say that the role is in an area, not what the team does.
GENERIC = {"investing", "other_finance"}

# (pattern on folded lowercase text, function, priority, confidence). The highest priority wins the primary slot;
# ties go to the earliest match. What a person does ("engineer", "legal") outranks the area they do it in
# ("global markets"), so a bank's developer is technology with sales & trading kept as a secondary function.
_RULES: list[tuple[str, str, int, str]] = [
    # what the intern does, outside finance
    (r"\bengineers?\b|(?<!business )developer|software|\bcyber|\bsecurity\b|cloud|devops|workstation|\bjava\b|website|"
     r"\bai\b|\bki\b|generative|automati[sz]ation|automatisierung|machine\s+learning|deep\s+learning|technolog|"
     r"infrastructure\s+support|digital\s+signature|cybersecurit|\btech\b", "technology", 90, "high"),
    (r"data\s+scien|data\s+analy|analytics|business\s+intelligence|\bdata\b", "data", 88, "high"),
    (r"human\s+resources|\bhr\b|\bpeople\b|talent|recruit|recursos\s+humanos|personas|\blearning\b", "hr", 90, "high"),
    (r"marketing|communication|copywrit|social\s+media|graphic\s+design|brand|affaires\s+publiques|public\s+affairs",
     "marketing", 89, "high"),
    (r"legal|lawyer|rechtsdienst|rechtdienst|compliance|\bafc\b|\baml\b|\bkyc\b|regulatory|governance|"
     r"general\s+secretary|privacy|juridique",
     "compliance_legal", 90, "high"),
    (r"\baudit", "compliance_legal", 89, "high"),
    (r"operations|\bops\b|customer\s+service|client\s+service|business\s+support|back\s+office|workplace|"
     r"fund\s+admin|administrati|dienstleistung|support\s+specialist|reconciliation|client\s+experience",
     "operations", 90, "high"),
    (r"investor\s+relations|investor\s+solutions|fundrais|syndication", "investor_relations", 90, "high"),
    (r"product\s+(manag|innovation|owner)|\bux\b|user\s+experience|product\s+operations", "product", 91, "high"),
    (r"\bsales\b(?!\s*(&|and|,)\s*trading)|business\s+develop|\bsdr\b|relationship\s+manag|b2c|"
     r"\bdistribution\b|vertrieb(?<!filialvertrieb)",
     "sales", 87, "high"),
    (r"\besg\b|sustainab|nature-based", "sustainability", 86, "high"),
    # Desk sales is markets work, unlike distribution or business-development sales.
    (r"(equity|equities|fixed\s+income|\bfx\b|rates|credit|structured(\s+\w+)?)\s+sales", "sales_trading", 88, "high"),
    (r"founder\s+associate|business\s+management|project\s+manag|program\s+manag|strateg", "strategy", 60, "high"),
    # finance teams whose title says exactly what they do
    (r"fund\s+finance|controlling|fp\s?&\s?a|financial\s+planning|consolidation|accounting|product\s+control|"
     r"group\s+finance|comptabilit|\bfinance\s*-\s*stage", "finance_control", 85, "high"),
    (r"m\s?&\s?a\b|mergers|fusions?\s+et\s+acquisitions|fusiones|uebernahmen", "m_and_a", 80, "high"),
    (r"restructuring|restructuration", "restructuring", 80, "high"),
    (r"equity\s+capital\s+markets?|\becm\b", "ecm", 80, "high"),
    (r"debt\s+capital\s+markets?|\bdcm\b|leveraged\s+finance|debt\s+advisory", "dcm", 80, "high"),
    (r"capital\s+markets?", "capital_markets", 72, "high"),
    (r"(equity|credit|investment|fixed\s+income)\s+research|research\s+analyst|finanzanalyse", "research", 80, "high"),
    (r"private\s+equity|buy-?out", "private_equity", 80, "high"),
    (r"venture\s+capital|\bvc\b", "venture_capital", 80, "high"),
    (r"hedge\s+fund", "hedge_funds", 80, "high"),
    (r"real\s+estate|immobili", "real_estate", 78, "high"),
    (r"secondaries|primaries|fund\s+of\s+funds|co-?invest|real\s+assets|infrastructure|alternative\s+invest",
     "alternatives", 75, "high"),
    (r"private\s+(credit|debt)|\bcredit\b(?!\s+research)|kredit|lending|credito", "credit", 76, "high"),
    (r"sales\s*(&|and|,)\s*trading|\btrading\b|\btrader\b|structuring|haendler|global\s+markets", "sales_trading", 79,
     "high"),
    (r"quantitativ|\bquant\b", "quant", 89, "high"),
    (r"portfolio\s+manag|asset\s+manag|fund\s+manag|gestion\s+d.actifs|sustainable\s+investing|"
     r"quantitative\s+investing", "asset_management", 76, "high"),
    (r"wealth\s+manag|vermoegensverwaltung|gestion\s+de\s+fortune|assistant(\(e\)|e)?\s+de\s+gestion|"
     r"banquier\s+patrimonial|private\s+clients|privatkunden", "wealth_management",
     78, "high"),
    (r"private\s+bank|banque\s+privee|banquier\s+prive|banca\s+privada", "private_banking", 79, "high"),
    (r"corporate\s+banking|commercial\s+banking|retail\s+banking|customer\s+banking|transaction\s+banking|"
     r"filialvertrieb", "banking", 78, "high"),
    (r"treasury|tresorerie|tesoreria", "treasury", 86, "high"),
    (r"\brisk|risque|risiko|riesgo", "risk", 81, "high"),
    (r"family\s+office", "family_office", 80, "high"),
    (r"investment\s+consult|pension\s+consult", "investment_consulting", 80, "high"),
    (r"corporate\s+finance|valuation|capital\s+solutions|secondary\s+advisory|"
     r"financial\s+(and\s+strategic\s+)?advisory|finanzas\s+corporativas",
     "corporate_finance", 74, "high"),
    (r"investment\s+bank|\bibd\b|banque\s+d.investissement|banca\s+de\s+inversion|"
     r"^banking\s+(analyst|associate|off|summer|intern)", "investment_banking", 70, "medium"),
    (r"sustainable\s+finance", "other_finance", 50, "medium"),
    (r"\binvestments?\b|investment[-\s]+(team|group|intern)", "investing", 40, "medium"),
]
_RES = [(re.compile(p, re.I), f, prio, conf) for p, f, prio, conf in _RULES]
# "IT" is only a technology role when written in capitals and not as a coverage sector ("Corporate Finance (IT
# Services)").
_IT = re.compile(r"\bIT\b(?!\s+Services)")
_COVERAGE = re.compile(r"\([^)]*\)")  # a parenthesised coverage area in a title, e.g. "(IT Services)"

# Description evidence: only finance functions, only specific phrases, and only when repeated.
_DESC_RULES = [(rx, f) for rx, f, prio, _ in _RES if FUNCTIONS[f]["finance"] and f not in GENERIC and prio >= 72]


def _matches(text: str, *, original: str = "") -> list[tuple[int, int, str, str, str]]:
    folded = fold(text)
    hits = []
    for rx, fn, prio, conf in _RES:
        m = rx.search(folded)
        if m:
            # Folding can change the length (ü -> ue); then the folded words are the evidence.
            ev = text[m.start():m.end()] if len(text) == len(folded) else m.group(0)
            hits.append((prio, m.start(), fn, conf, ev))
    m = _IT.search(_COVERAGE.sub(" ", original or text))
    if m:
        hits.append((90, m.start(), "technology", "high", "IT"))
    return hits


def _result(function: str, functions: list[str], confidence: str, method: str, evidence: str | None) -> dict:
    return {"function": function, "functions": functions, "confidence": confidence, "method": method,
            "evidence": (evidence or "")[:200] or None, "version": VERSION}


def _from(hits: list) -> tuple[str, list[str], str, str] | None:
    if not hits:
        return None
    hits = sorted(hits, key=lambda h: (-h[0], h[1]))
    ordered = list(dict.fromkeys(h[2] for h in hits))
    prio, _, primary, conf, ev = hits[0]
    return primary, ordered, conf, ev


# After a dash or bracket, titles often name the industry covered ("M&A Intern - Technology") or the client group
# ("Private Banking - Client Relationship Management"). When the head of the title names a finance function, such
# words in the tail are coverage, not the work.
_TAIL = re.compile(r"\s[-–|]\s|\(")
_COVERAGE_FUNCTIONS = {"technology", "data", "sales"}


def _title_hits(title: str) -> list:
    hits = _matches(title, original=title)
    m = _TAIL.search(title)
    if not m:
        return hits
    head = len(fold(title[:m.start()]))
    if any(h[1] < head and FUNCTIONS[h[2]]["finance"] and h[2] not in GENERIC for h in hits):
        hits = [h for h in hits if not (h[1] >= head and h[2] in _COVERAGE_FUNCTIONS)]
    return hits


def classify(title: str, *, department: str = "", description: str = "") -> dict:
    """The role function of one posting. Pure; see the module docstring for the layers."""
    title = title or ""
    found = _from(_title_hits(title))
    if found and found[0] not in GENERIC:
        primary, ordered, conf, ev = found
        return _result(primary, ordered, conf, "title", ev)
    generic = found
    if department.strip():
        dep = _from(_matches(department))
        if dep and dep[0] not in GENERIC:
            return _result(dep[0], list(dict.fromkeys(dep[1] + (generic[1] if generic else []))), "medium",
                           "department", f"department: {department.strip()}")
    if generic:
        primary, ordered, conf, ev = generic
        return _result(primary, ordered, conf, "title", ev)
    text = fold(description or "")[:3000]
    counts = {}
    for rx, fn in _DESC_RULES:
        n = len(rx.findall(text))
        if n:
            counts[fn] = counts.get(fn, 0) + n
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        fn = ranked[0][0]
        return _result(fn, [f for f, _ in ranked], "low", "description",
                       f"the description names {FUNCTIONS[fn]['label'].lower()} {ranked[0][1]} times")
    return _result("unknown", [], "none", "none", None)


def taxonomy() -> dict:
    return {"version": VERSION, "families": FAMILIES,
            "functions": [{"id": k, **v} for k, v in FUNCTIONS.items()]}


# --- persistence -------------------------------------------------------------------------------------------------

def _hash(title: str, department: str, description: str) -> str:
    return hashlib.sha256(f"{title}\x00{department}\x00{description}".encode()).hexdigest()[:20]


def store(conn: sqlite3.Connection, now: datetime, opp_id: str, *, department: str | None = None) -> bool:
    """(Re)classify one opportunity when its text, department or the classifier version changed. Idempotent.

    An AI classification is kept while the text it was read from is unchanged and the rules still find nothing
    better than low confidence. A changed function is written to the opportunity's history.
    """
    o = one(conn, "SELECT title, description FROM opportunities WHERE id = ?", (opp_id,))
    if o is None:
        return False
    cur = one(conn, "SELECT * FROM opportunity_roles WHERE opportunity_id = ?", (opp_id,))
    dept = department if department is not None else (cur["department"] if cur else "") or ""
    h = _hash(o["title"], dept, o["description"] or "")
    if cur and cur["content_hash"] == h and cur["version"] == VERSION:
        return False
    r = classify(o["title"], department=dept, description=o["description"] or "")
    if cur and cur["method"] == "ai" and cur["content_hash"] == h and r["confidence"] in ("low", "none"):
        conn.execute("UPDATE opportunity_roles SET version = ? WHERE opportunity_id = ?", (VERSION, opp_id))
        return False
    _write(conn, now, opp_id, r, dept, h, model=None, cur=cur)
    return True


def apply_ai(conn: sqlite3.Connection, now: datetime, opp_id: str, function: str, quote: str,
             model: str | None) -> bool:
    """Gemini's reading, used only when the rules ended at low confidence or nothing, or at a generic function."""
    if function not in FUNCTIONS:
        return False
    cur = one(conn, "SELECT * FROM opportunity_roles WHERE opportunity_id = ?", (opp_id,))
    if cur is None:
        store(conn, now, opp_id)
        cur = one(conn, "SELECT * FROM opportunity_roles WHERE opportunity_id = ?", (opp_id,))
    if cur is None or (cur["confidence"] not in ("low", "none") and cur["function"] not in GENERIC):
        return False
    secondary = [f for f in loads(cur["functions"], []) if f != function]
    r = _result(function, [function, *secondary], "medium", "ai", quote)
    _write(conn, now, opp_id, r, cur["department"] or "", cur["content_hash"], model=model, cur=cur)
    return True


def _write(conn, now, opp_id, r, dept, h, *, model, cur) -> None:
    changed = cur is None or cur["function"] != r["function"]
    conn.execute(
        "INSERT INTO opportunity_roles (opportunity_id, function, functions, confidence, method, evidence, department, "
        "content_hash, version, model, classified_at, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(opportunity_id) DO UPDATE SET function = excluded.function, functions = excluded.functions, "
        "confidence = excluded.confidence, method = excluded.method, evidence = excluded.evidence, "
        "department = excluded.department, content_hash = excluded.content_hash, version = excluded.version, "
        "model = excluded.model, classified_at = excluded.classified_at, "
        "changed_at = CASE WHEN opportunity_roles.function = excluded.function THEN opportunity_roles.changed_at "
        "ELSE excluded.changed_at END",
        (opp_id, r["function"], dumps(r["functions"]), r["confidence"], r["method"], r["evidence"], dept or None, h,
         r["version"], model, iso(now), iso(now)))
    if changed and cur is not None:
        conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, field, old_value, new_value, note) "
                     "VALUES (?, ?, 'changed', 'role', ?, ?, ?)",
                     (opp_id, iso(now), cur["function"], r["function"], f"{r['method']} classification {VERSION}"))


def refresh(conn: sqlite3.Connection, now: datetime) -> int:
    """Classify every opportunity that has no current classification. Cheap: rules only, unchanged rows skipped."""
    n = 0
    for (oid,) in conn.execute("SELECT id FROM opportunities ORDER BY id").fetchall():
        n += store(conn, now, oid)
    return n


def for_export(row) -> dict:
    return {"function": row["function"], "functions": loads(row["functions"], []), "confidence": row["confidence"],
            "method": row["method"], "evidence": row["evidence"], "version": row["version"],
            "changed_at": row["changed_at"]}
