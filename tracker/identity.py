"""Opportunity identity: canonical URLs, title keys and conservative duplicate matching.

Matching order (first hit wins):
  1. the same posting at the same source (source_id, external_id)
  2. the same canonical URL at the same employer
  3. the same ATS requisition id at the same employer
  4. cross-source: same employer, same normalised title, compatible place and intake year, and the candidate
     is not already listed by this source (two postings with one title at one source are different roles)
Near misses (similar but not identical titles) are never merged: the new record is created and flagged
"possible_duplicate:<id>" for review instead, because merging two distinct opportunities loses one of them.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tracker.db import loads, one
from tracker.taxonomy import fold

_TRACKING = re.compile(r"^(utm_\w+|gh_src|source|src|ref|refs|trk|trackingid|lever-source|lever-origin|"
                       r"iis|iisn|codes|mode|fbclid|gclid|mc_cid|mc_eid|_hs\w+)$", re.I)
_GENDER = re.compile(r"\((?:[mwfdxhae]\s*/\s*){1,3}[mwfdxhae]\)|\b(?:m|w|f|h)\s*/\s*(?:w|f|m|d|x|h)(?:\s*/\s*[dx])?\b"
                     r"|\(all genders?\)|\(gn\)", re.I)
SIMILAR = 0.88


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING.match(k))
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower() + (f":{parts.port}" if parts.port else ""),
                       path, urlencode(query), ""))


def opportunity_id(employer_id: str, url: str) -> str:
    return hashlib.sha1(f"{employer_id}|{url}".encode()).hexdigest()[:16]


def title_key(title: str) -> str:
    t = _GENDER.sub(" ", title)
    t = re.sub(r"(?<!\d)20\d\d(\s*[/–-]\s*(20)?\d\d)?(?!\d)", " ", t)  # years are compared separately
    return " ".join(re.findall(r"[a-z0-9&]+", fold(t)))


def _places_compatible(a_city, a_countries, b_city, b_countries) -> bool:
    if a_city and b_city and fold(a_city) != fold(b_city):
        return False
    if a_countries and b_countries and not set(a_countries) & set(b_countries):
        return False
    return True


def find_match(conn: sqlite3.Connection, *, employer_id: str, source_id: str, external_id: str, url: str,
               title: str, city: str | None, countries: list[str], year: int | None,
               requisition_id: str | None) -> tuple[str | None, str | None, str | None]:
    """(opportunity id, how it matched, possible-duplicate id). All None when it is new."""
    row = one(conn, "SELECT opportunity_id FROM source_postings WHERE source_id = ? AND external_id = ?",
              (source_id, external_id))
    if row and row["opportunity_id"]:
        return row["opportunity_id"], "same_posting", None
    row = one(conn, "SELECT id FROM opportunities WHERE employer_id = ? AND canonical_url = ?",
              (employer_id, canonical_url(url)))
    if row:
        return row["id"], "same_url", None
    if requisition_id:
        for r in conn.execute("SELECT sp.opportunity_id, sp.structured FROM source_postings sp JOIN sources s ON "
                              "s.id = sp.source_id WHERE s.employer_id = ? AND sp.opportunity_id IS NOT NULL",
                              (employer_id,)):
            if str(loads(r["structured"], {}).get("requisition_id") or "") == str(requisition_id):
                return r["opportunity_id"], "same_requisition", None
    key = title_key(title)
    possible = None
    candidates = conn.execute(
        "SELECT id, title_key, city, countries, intake_year FROM opportunities WHERE employer_id = ? "
        "AND excluded_reason IS NULL", (employer_id,)).fetchall()
    for c in candidates:
        if not _places_compatible(city, countries, c["city"], loads(c["countries"], [])):
            continue
        if year and c["intake_year"] and year != c["intake_year"]:
            continue
        if c["title_key"] == key:
            listed_here = one(conn, "SELECT 1 FROM source_postings WHERE source_id = ? AND opportunity_id = ? "
                              "AND present = 1", (source_id, c["id"]))
            if listed_here is None:
                return c["id"], "cross_source_title", None
        elif possible is None and SequenceMatcher(None, key, c["title_key"]).ratio() >= SIMILAR:
            possible = c["id"]
    return None, None, possible
