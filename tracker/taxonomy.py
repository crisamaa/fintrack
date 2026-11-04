"""Reference data and deterministic classifiers: sectors, internship types, countries, places and languages.

Everything here is rule-based and multilingual (EN/DE/FR/IT/ES). It runs before any AI call, so most postings from
structured job boards are classified without spending anything; AI only fills what the rules leave unknown.
"""
from __future__ import annotations

import re
import unicodedata

# --- sectors -------------------------------------------------------------------------------------------------------
# Overlapping: a role or an employer can carry several. The key order is the display order.
SECTORS: dict[str, str] = {
    "investment_banking": "Investment banking",
    "m_and_a": "M&A advisory",
    "ecm": "Equity capital markets",
    "dcm": "Debt capital markets",
    "sales_trading": "Sales & trading",
    "research": "Equity & credit research",
    "asset_management": "Asset management",
    "wealth_management": "Wealth management",
    "private_banking": "Private banking",
    "private_equity": "Private equity",
    "venture_capital": "Venture capital",
    "hedge_funds": "Hedge funds",
    "alternatives": "Alternative investments",
    "family_office": "Family office",
    "corporate_finance": "Corporate finance",
    "treasury": "Treasury",
    "risk": "Financial risk",
    "credit": "Credit analysis",
    "investment_consulting": "Investment consulting",
    "banking": "Commercial & retail banking",
    "fintech": "Fintech",
    "other_finance": "Other finance",
}

# The four sectors of the original firm list, mapped onto the taxonomy.
LEGACY_SECTORS: dict[str, list[str]] = {
    "banking": ["banking"],
    "private_equity": ["private_equity"],
    "finance": ["asset_management", "wealth_management"],
    "fintech": ["fintech"],
}

_SECTOR_RULES: list[tuple[str, list[str]]] = [
    (r"m\s?&\s?a|mergers|fusions?\s+et\s+acquisitions|fusiones|übernahmen", ["m_and_a", "investment_banking"]),
    (r"investment\s+bank|ibd\b|banque\s+d.investissement|banca\s+de\s+inversi[oó]n|banca\s+d.investimento",
     ["investment_banking"]),
    (r"\becm\b|equity\s+capital\s+markets?", ["ecm", "investment_banking"]),
    (r"\bdcm\b|debt\s+capital\s+markets?|leveraged\s+finance", ["dcm", "investment_banking"]),
    (r"sales\s*(&|and)\s*trading|\btrading\b|global\s+markets|\bmarkets\b|händler|trader", ["sales_trading"]),
    (r"(equity|credit|investment|fixed\s+income)\s+research|research\s+analyst|finanzanalyse", ["research"]),
    (r"asset\s+management|portfolio\s+manag|fund\s+manag|gestion\s+d.actifs|vermögensanlage", ["asset_management"]),
    (r"wealth\s+manag|vermögensverwaltung|gestion\s+de\s+fortune|gesti[oó]n\s+patrimonial", ["wealth_management"]),
    (r"private\s+bank|banque\s+priv[ée]e|banca\s+privada", ["private_banking"]),
    (r"private\s+equity|buy-?out", ["private_equity"]),
    (r"venture\s+capital|\bvc\b", ["venture_capital"]),
    (r"hedge\s+fund", ["hedge_funds"]),
    (r"alternative\s+invest|real\s+assets|infrastructure\s+invest|private\s+(debt|credit|markets)", ["alternatives"]),
    (r"family\s+office", ["family_office"]),
    (r"corporate\s+finance|fp\s?&\s?a|financial\s+planning|controlling|finanzas\s+corporativas", ["corporate_finance"]),
    (r"treasury|trésorerie|tesorer[ií]a|treasur", ["treasury"]),
    (r"\brisk\b|risiko|risque|riesgos?", ["risk"]),
    (r"\bcredit\b|kredit|crédito|lending", ["credit"]),
    (r"investment\s+consult|pension\s+consult", ["investment_consulting"]),
]
_SECTOR_RES = [(re.compile(p, re.I), s) for p, s in _SECTOR_RULES]


def classify_sectors(text: str) -> list[str]:
    """Sectors named in a job title or description, in taxonomy order. Empty when nothing matches."""
    found: set[str] = set()
    for rx, sectors in _SECTOR_RES:
        if rx.search(text or ""):
            found.update(sectors)
    return [s for s in SECTORS if s in found]


# --- internship types ----------------------------------------------------------------------------------------------
INTERNSHIP_TYPES: dict[str, str] = {
    "summer": "Summer internship",
    "spring": "Spring internship",
    "winter": "Winter internship",
    "off_cycle": "Off-cycle internship",
    "insight": "Insight / spring week",
    "working_student": "Working student",
    "part_time": "Part-time internship",
    "graduate": "Graduate / trainee programme",
    "unknown": "Internship",
}

_TYPE_RULES: list[tuple[str, str]] = [
    (r"insight|spring\s+week|discovery\s+(day|week|program)|open\s+day", "insight"),
    (r"off[-\s]?cycle", "off_cycle"),
    (r"werkstudent|working\s+student|student\s+assistant|studentische", "working_student"),
    (r"part[-\s]?time|teilzeit|temps\s+partiel|medio\s+tiempo|tiempo\s+parcial", "part_time"),
    (r"summer|sommer|\bété\b|estiva|verano", "summer"),
    (r"winter|hiver|invierno", "winter"),
    (r"\bspring\b|frühling|printemps|primavera", "spring"),
    (r"graduate\s+(program|programme|scheme)|trainee\s*(program|programme)?|berufseinstieg|direct\s+entry", "graduate"),
]
_TYPE_RES = [(re.compile(p, re.I), t) for p, t in _TYPE_RULES]

# Legacy "season" values map one-to-one onto internship types.
LEGACY_SEASONS = {"summer": "summer", "spring": "spring", "winter": "winter", "off_cycle": "off_cycle",
                  "unknown": "unknown"}


def classify_type(title: str) -> str:
    for rx, t in _TYPE_RES:
        if rx.search(title or ""):
            return t
    return "unknown"


_YEAR = re.compile(r"(?<!\d)(20[2-4]\d)(?:\s*[/–-]\s*(?:20)?(\d{2}))?(?!\d)")


def intake_year(title: str) -> int | None:
    """The internship year named in a title ("2027 Summer Analyst", "Stage 2026/27" -> 2026)."""
    m = _YEAR.search(title or "")
    return int(m.group(1)) if m else None


# --- countries & places --------------------------------------------------------------------------------------------
EEA = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO".split())
# Countries the tracker collects by default: Switzerland, EU/EEA, the UK and Peru. Postings elsewhere are kept only
# when they explicitly offer sponsorship (see tracker.requirements).
COUNTRIES_OF_INTEREST = EEA | {"CH", "GB", "PE"}


def fold(text: str) -> str:
    """Lowercase ASCII; German umlauts transliterated, other accents dropped."""
    text = text.translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
                                         "ß": "ss", "æ": "ae", "ø": "o", "å": "a", "ł": "l"}))
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


# City or country name (folded) -> ISO code. Covers the finance hubs of the countries of interest.
_COUNTRY_NAMES: dict[str, str] = {
    "CH": "switzerland, schweiz, suisse, svizzera, suiza",
    "GB": "united kingdom, uk, england, scotland, great britain",
    "PE": "peru",
    "DE": "germany, deutschland, allemagne, alemania",
    "FR": "france, frankreich, francia",
    "IT": "italy, italia, italien",
    "ES": "spain, espana, spanien",
    "NL": "netherlands, nederland, niederlande",
    "LU": "luxembourg, luxemburg",
    "IE": "ireland, irland",
    "BE": "belgium, belgique, belgien",
    "AT": "austria, oesterreich",
    "SE": "sweden, sverige",
    "DK": "denmark, danmark",
    "NO": "norway, norge",
    "FI": "finland, suomi",
    "PL": "poland, polska",
    "PT": "portugal",
    "CZ": "czech republic, czechia",
    "GR": "greece",
    "HU": "hungary",
    "RO": "romania",
    "LI": "liechtenstein",
    "MT": "malta",
    "CY": "cyprus",
    "IS": "iceland",
    "EE": "estonia",
    "LV": "latvia",
    "LT": "lithuania",
    "SK": "slovakia",
    "SI": "slovenia",
    "HR": "croatia",
    "BG": "bulgaria",
    "US": "united states, usa",
    "SG": "singapore",
    "HK": "hong kong",
}
_CITY_NAMES: dict[str, str] = {
    "CH": "zurich, zuerich, zurigo, geneva, geneve, genf, ginevra, basel, bale, lugano, bern, berne, zug, lausanne, "
          "st gallen, lucerne, luzern, chiasso, neuchatel, winterthur, pfaeffikon",
    "GB": "london, edinburgh, manchester, birmingham, glasgow, leeds",
    "PE": "lima, arequipa, trujillo, san isidro, miraflores",
    "DE": "frankfurt, frankfurt am main, munich, muenchen, berlin, hamburg, duesseldorf, cologne, koeln, stuttgart",
    "FR": "paris, lyon, marseille, la defense",
    "IT": "milan, milano, rome, roma, turin, torino",
    "ES": "madrid, barcelona",
    "NL": "amsterdam, rotterdam, the hague",
    "IE": "dublin",
    "BE": "brussels, bruxelles, antwerp",
    "AT": "vienna, wien",
    "SE": "stockholm, gothenburg",
    "DK": "copenhagen",
    "NO": "oslo",
    "FI": "helsinki",
    "PL": "warsaw, warszawa, krakow",
    "PT": "lisbon, lisboa, porto",
    "CZ": "prague, praha",
    "GR": "athens",
    "HU": "budapest",
    "RO": "bucharest",
    "LI": "vaduz",
    "MT": "valletta",
    "CY": "limassol, nicosia",
    "EE": "tallinn",
    "LV": "riga",
    "LT": "vilnius",
    "US": "new york, boston, chicago, san francisco",
}
_COUNTRY_WORDS = {n.strip(): c for c, names in _COUNTRY_NAMES.items() for n in names.split(",")}
_CITY_WORDS = {n.strip(): c for c, names in _CITY_NAMES.items() for n in names.split(",")}
_PLACES = {**_COUNTRY_WORDS, **_CITY_WORDS}
_CITY_DISPLAY = {"frankfurt am main": "Frankfurt", "st gallen": "St. Gallen", "zuerich": "Zurich", "zurich": "Zurich", "geneve": "Geneva", "genf": "Geneva", "muenchen": "Munich",
                 "koeln": "Cologne", "duesseldorf": "Düsseldorf", "milano": "Milan", "roma": "Rome", "wien": "Vienna",
                 "bruxelles": "Brussels", "lisboa": "Lisbon", "warszawa": "Warsaw", "praha": "Prague"}
_ISO_CODES = COUNTRIES_OF_INTEREST | {"US", "SG", "HK"}
_ISO = re.compile(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])")


def parse_location(text: str) -> tuple[str | None, list[str]]:
    """(city, country codes) recognised in a free-text location such as "Zurich, Switzerland; London".

    Returns (None, []) when nothing is recognised; never guesses.
    """
    if not text:
        return None, []
    folded = fold(text)
    countries: list[str] = []
    city: str | None = None
    for raw in re.split(r"[,;/|()\n]+|\s[-–]\s", folded):
        t = " ".join(raw.replace(".", "").split())
        if not t:
            continue
        words = t.split()
        # longest known name first: "frankfurt am main" before "frankfurt", "new york" before "york"
        spans = [" ".join(words[i:j]) for i in range(len(words)) for j in range(len(words), i, -1)]
        for c in spans:
            code = _PLACES.get(c)
            if code:
                if code not in countries:
                    countries.append(code)
                if city is None and c in _CITY_WORDS:
                    city = _CITY_DISPLAY.get(c, c.title())
                break
    for m in _ISO.finditer(text):
        code = "GB" if m.group(1) == "UK" else m.group(1)
        if code in _ISO_CODES and code not in countries:
            countries.append(code)
    return city, countries


# --- languages -----------------------------------------------------------------------------------------------------
LANGUAGE_NAMES: dict[str, list[str]] = {
    "en": ["english", "englisch", "anglais", "ingles", "inglese"],
    "de": ["german", "deutsch", "allemand", "aleman", "tedesco"],
    "fr": ["french", "franzoesisch", "francais", "frances", "francese"],
    "it": ["italian", "italienisch", "italien", "italiano"],
    "es": ["spanish", "spanisch", "espagnol", "espanol", "castellano", "spagnolo"],
    "pt": ["portuguese", "portugiesisch", "portugais", "portugues"],
    "nl": ["dutch", "niederlaendisch", "neerlandais"],
    "sv": ["swedish", "schwedisch"],
    "da": ["danish", "daenisch"],
    "pl": ["polish", "polnisch"],
    "zh": ["mandarin", "chinese", "chinesisch"],
}
_LANG_WORD = {w: code for code, words in LANGUAGE_NAMES.items() for w in words}
_REQ_CUE = re.compile(
    r"(fluen\w*|proficien\w*|native|business[-\s]level|excellent|strong|good|very\s+good|advanced|working\s+knowledge|"
    r"command\s+of|verhandlungssicher|fliessend|sehr\s+gute|gute|stilsicher|courant|maitrise|excellente?s?|bonne|"
    r"dominio|fluido|avanzado|nivel|ottima|buona|required|requis|erforderlich|indispensable|imprescindible|"
    r"must\s+speak|speak)", re.I)


def required_languages(text: str) -> list[tuple[str, str]]:
    """(language code, evidence sentence) for languages the text states as a requirement.

    A language counts only when a proficiency or requirement cue is in the same sentence, so "our German clients"
    is not read as a requirement.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sentence in re.split(r"(?<=[.!?;•\n])\s+|\s+[•·▪]\s+", text or ""):
        folded = fold(sentence)
        if not _REQ_CUE.search(folded):
            continue
        for word in re.findall(r"[a-z]+", folded):
            code = _LANG_WORD.get(word)
            if code and code not in seen:
                seen.add(code)
                out.append((code, " ".join(sentence.split())[:300]))
    return out
