"""Employer names and web identities: folding, matching, slugs and registrable domains."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from tracker.taxonomy import fold as _fold

# Legal forms, filler words and country markers that say nothing about which firm it is.
_STOP = frozenset("""ag sa sarl sas spa srl sl nv bv se ab as asa oyj plc ltd limited llc llp lp inc corp corporation co
company gmbh kg ohg aktiengesellschaft genossenschaft societe anonyme cie bank banque banca banco the of and de la le
et und von del della schweiz suisse svizzera switzerland designated activity group holding holdings saa sac sab saf
peru""".split())
_LEGAL = frozenset("""ag sa sarl sas spa srl sl nv bv se ab as asa oyj plc ltd limited llc llp lp inc corp corporation co
company gmbh kg ohg aktiengesellschaft genossenschaft saa sac""".split())


def fold(text: str) -> str:
    """Lowercase ASCII with German umlauts transliterated (Bär -> baer), other accents dropped, "S.A." -> "sa"."""
    return re.sub(r"(?<![a-z])([a-z])\.([a-z])\.?(?![a-z])", r"\1\2", _fold(text))


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


def make_id(name: str) -> str:
    """A stable lowercase slug from a firm name: folded, legal forms dropped, words joined with hyphens."""
    words = [w for w in re.findall(r"[a-z0-9]+", fold(name.split(",")[0])) if w not in _LEGAL]
    return "-".join(words) or "firm"


# Second-level public suffixes that matter for the countries we cover.
_MULTI_SUFFIX = {"co.uk", "org.uk", "ac.uk", "com.pe", "gob.pe", "edu.pe", "org.pe", "com.es", "com.pl", "co.at",
                 "com.br", "com.ar", "com.mx", "com.sg", "com.hk", "com.au"}


def registrable_domain(url_or_host: str) -> str | None:
    """"https://careers.ubs.com/x" -> "ubs.com"; "www.bcp.com.pe" -> "bcp.com.pe". None for IPs or junk."""
    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    if not host:
        return None
    host = host.lower().rstrip(".")
    if re.fullmatch(r"[\d.]+|\[.*\]|[0-9a-f:]+", host) or "." not in host:
        return None
    parts = host.split(".")
    n = 3 if ".".join(parts[-2:]) in _MULTI_SUFFIX else 2
    return ".".join(parts[-n:]) if len(parts) >= n else host
