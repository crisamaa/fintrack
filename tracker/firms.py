"""The firm list: loading, validation, saving, and telling whether two names are the same firm."""
import os
import re
import unicodedata
from pathlib import Path

import yaml

from tracker.models import ALLOWED_COUNTRIES, Firm

_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
                           "æ": "ae", "Æ": "AE", "ø": "o", "Ø": "O", "å": "a", "Å": "A", "ł": "l", "Ł": "L"})
# Legal forms, filler words and "Switzerland" markers that say nothing about which firm it is.
_STOP = frozenset("""ag sa sarl sas spa srl sl nv bv se ab as asa oyj plc ltd limited llc llp lp inc corp corporation co
company gmbh kg ohg aktiengesellschaft genossenschaft societe anonyme cie bank banque banca banco the of and de la le
et und von del della schweiz suisse svizzera switzerland designated activity group holding holdings""".split())


def fold(text: str) -> str:
    """Lowercase ASCII with German umlauts transliterated (Bär -> baer) and other accents dropped."""
    text = text.translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"(?<![a-z])([a-z])\.([a-z])\.?(?![a-z])", r"\1\2", text)  # "S.A." -> "sa"


def name_tokens(name: str) -> list[str]:
    """The tokens that identify a firm: folded, without legal forms and filler words."""
    words = re.findall(r"[a-z0-9]+", fold(name))
    core = [w for w in words if w not in _STOP]
    return core or words


def name_key(name: str) -> str:
    return "".join(name_tokens(name))


def same_firm(a: str, b: str) -> bool:
    """True when one name's identifying tokens are all in the other's ("Vontobel" / "Bank Vontobel AG")."""
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big and len("".join(small)) >= 3


_LEGAL = frozenset("""ag sa sarl sas spa srl sl nv bv se ab as asa oyj plc ltd limited llc llp lp inc corp corporation co
company gmbh kg ohg aktiengesellschaft genossenschaft""".split())


def make_id(name: str) -> str:
    """A stable lowercase slug from a firm name: folded, legal forms dropped, words joined with hyphens."""
    words = [w for w in re.findall(r"[a-z0-9]+", fold(name.split(",")[0])) if w not in _LEGAL]
    return "-".join(words) or "firm"


def _check(firm: Firm) -> None:
    if not _ID.fullmatch(firm.id):
        raise ValueError(f"{firm.id!r}: id must be a lowercase slug")
    if firm.country not in ALLOWED_COUNTRIES:
        raise ValueError(f"{firm.id}: country {firm.country!r} is not allowed")
    if firm.source == "page":
        if not firm.careers_url:
            raise ValueError(f"{firm.id}: page firm needs careers_url")
    elif not firm.slug:
        raise ValueError(f"{firm.id}: {firm.source} firm needs slug")
    elif firm.source == "workday" and (not firm.site or "/" not in firm.slug):
        raise ValueError(f"{firm.id}: workday firm needs site and slug 'tenant/board'")


def load_firms(path: Path) -> list[Firm]:
    raw = yaml.safe_load(Path(path).read_text())
    items = raw.get("firms") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list of firms")
    firms, seen = [], set()
    for item in items:
        firm = Firm.model_validate(item)  # an unknown sector or source fails here (ValidationError is a ValueError)
        _check(firm)
        if firm.id in seen:
            raise ValueError(f"duplicate firm id {firm.id!r}")
        seen.add(firm.id)
        firms.append(firm)
    return firms


def write_yaml(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    os.replace(tmp, path)


def save_firms(path: Path, firms: list[Firm]) -> None:
    write_yaml(path, [f.model_dump(exclude_none=True) for f in firms])
