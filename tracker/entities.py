"""Employer and source registry: resolution (is this a firm we know?), registration, aliases and candidates.

Resolution is conservative. In order: exact registrable domain of the firm's own website; exact folded name or
alias; then a token-subset name match ("Vontobel" / "Bank Vontobel AG") only when it is unambiguous and the
countries do not conflict. Shared hosting domains (ATS hosts, aggregators) never count as a firm's domain.
Anything less certain becomes an employer_candidate for discovery to verify, never a silent merge.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from tracker.connectors import SourceRef
from tracker.db import audit, iso, one
from tracker.names import make_id, name_key, registrable_domain, same_firm

# Domains that host many firms' pages: never evidence of who the employer is.
SHARED_DOMAINS = {"greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "smartrecruiters.com",
                  "recruitee.com", "personio.de", "personio.com", "linkedin.com", "indeed.com", "glassdoor.com",
                  "efinancialcareers.com", "efinancialcareers.ch", "jobs.ch", "jobup.ch", "stepstone.de",
                  "welcometothejungle.com", "google.com", "bebee.com", "talent.com", "computrabajo.com",
                  "bumeran.com.pe", "successfactors.com", "successfactors.eu", "taleo.net", "icims.com",
                  "oraclecloud.com", "jobvite.com", "workable.com", "teamtailor.com", "join.com", "softgarden.io"}


def employer_domain(url: str | None) -> str | None:
    d = registrable_domain(url) if url else None
    return d if d and d not in SHARED_DOMAINS else None


def resolve(conn: sqlite3.Connection, name: str | None, *, website: str | None = None,
            country: str | None = None) -> str | None:
    domain = employer_domain(website)
    if domain:
        rows = conn.execute("SELECT id FROM employers WHERE domain = ? AND status = 'active'", (domain,)).fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
    if not name:
        return None
    key = name_key(name)
    if not key:
        return None
    rows = conn.execute("SELECT id FROM employers WHERE name_key = ? AND status = 'active' UNION "
                        "SELECT employer_id FROM employer_aliases a JOIN employers e ON e.id = a.employer_id "
                        "WHERE a.alias_key = ? AND e.status = 'active'", (key, key)).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        return None  # ambiguous: leave it to a human or to more evidence
    matches = [r for r in conn.execute("SELECT id, name, hq_country FROM employers WHERE status = 'active'")
               if same_firm(name, r["name"]) and (not country or not r["hq_country"] or r["hq_country"] == country
                                                  or _has_location(conn, r["id"], country))]
    return matches[0]["id"] if len(matches) == 1 else None


def _has_location(conn, employer_id: str, country: str) -> bool:
    return one(conn, "SELECT 1 FROM employer_locations WHERE employer_id = ? AND country = ?",
               (employer_id, country)) is not None


def follow_merges(conn: sqlite3.Connection, employer_id: str) -> str:
    for _ in range(10):
        row = one(conn, "SELECT merged_into FROM employers WHERE id = ?", (employer_id,))
        if not row or not row["merged_into"]:
            return employer_id
        employer_id = row["merged_into"]
    return employer_id


def new_employer_id(conn: sqlite3.Connection, name: str) -> str:
    base = make_id(name)
    fid, n = base, 2
    while one(conn, "SELECT 1 FROM employers WHERE id = ?", (fid,)):
        fid, n = f"{base}-{n}", n + 1
    return fid


def register_employer(conn: sqlite3.Connection, now: datetime, *, name: str, discovered_via: str,
                      employer_id: str | None = None, website: str | None = None, country: str | None = None,
                      sectors: list[str] | None = None, sector_method: str = "seed", priority: int = 2,
                      evidence: str | None = None, size_hint: str | None = None,
                      access_note: str | None = None) -> str:
    """Insert an employer (or update the given id's descriptive fields). Returns its id."""
    eid = employer_id or new_employer_id(conn, name)
    exists = one(conn, "SELECT id FROM employers WHERE id = ?", (eid,))
    domain = employer_domain(website)
    if exists:
        conn.execute("UPDATE employers SET name = ?, name_key = ?, website = COALESCE(?, website), "
                     "domain = COALESCE(?, domain), hq_country = COALESCE(?, hq_country), priority = ?, "
                     "access_note = ?, size_hint = COALESCE(?, size_hint), updated_at = ? WHERE id = ?",
                     (name, name_key(name), website, domain, country, priority, access_note, size_hint, iso(now), eid))
    else:
        conn.execute("INSERT INTO employers (id, name, name_key, website, domain, hq_country, priority, access_note, "
                     "size_hint, discovered_via, discovery_evidence, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (eid, name, name_key(name), website, domain, country, priority, access_note, size_hint,
                      discovered_via, evidence, iso(now), iso(now)))
        audit(conn, now, "register", "employer", eid, {"name": name, "via": discovered_via, "evidence": evidence})
    add_alias(conn, eid, name, discovered_via)
    for s in sectors or []:
        conn.execute("INSERT OR IGNORE INTO employer_sectors VALUES (?, ?, ?)", (eid, s, sector_method))
    if country:
        conn.execute("INSERT OR IGNORE INTO employer_locations VALUES (?, ?, '', ?)", (eid, country, discovered_via))
    return eid


def add_alias(conn: sqlite3.Connection, employer_id: str, alias: str, source: str) -> None:
    key = name_key(alias)
    if key:
        conn.execute("INSERT OR IGNORE INTO employer_aliases VALUES (?, ?, ?, ?)", (employer_id, alias, key, source))


def register_source(conn: sqlite3.Connection, now: datetime, ref: SourceRef, *, employer_id: str | None,
                    discovery_method: str, evidence: str | None = None, association: str = "verified",
                    category: str = "careers", priority: int = 2, discovered_url: str | None = None,
                    access: str = "public", enabled: bool = True) -> tuple[str, bool]:
    """Insert a source if (kind, url) or its id is new. Returns (source id, created)."""
    row = one(conn, "SELECT id FROM sources WHERE id = ? OR (kind = ? AND url = ?)", (ref.id, ref.kind, ref.url))
    if row:
        return row["id"], False
    conn.execute(
        "INSERT INTO sources (id, employer_id, association, kind, board, site, url, discovered_url, category, access, "
        "discovery_method, discovery_evidence, enabled, health, priority, next_check_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ref.id, employer_id, association, ref.kind, ref.board, ref.site, ref.url, discovered_url or ref.url, category,
         access, discovery_method, evidence, 1 if enabled else 0, "unknown" if enabled else "disabled", priority,
         iso(now), iso(now), iso(now)))
    audit(conn, now, "register", "source", ref.id, {"employer": employer_id, "method": discovery_method,
                                                     "evidence": evidence})
    return ref.id, True


def add_candidate(conn: sqlite3.Connection, now: datetime, *, name: str, origin: str, country: str | None = None,
                  website: str | None = None, evidence_url: str | None = None, evidence: str | None = None,
                  sector_hint: str | None = None) -> int | None:
    """Queue a name for discovery. Known firms and repeats are ignored. Returns the candidate id when new."""
    key = name_key(name)
    if not key or resolve(conn, name, website=website, country=country):
        return None
    cur = conn.execute(
        "INSERT OR IGNORE INTO employer_candidates (name, name_key, country, website, sector_hint, origin, "
        "evidence_url, evidence, created_at, updated_at, next_attempt_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, key, country, website, sector_hint, origin, evidence_url, (evidence or "")[:500] or None, iso(now),
         iso(now), iso(now)))
    return cur.lastrowid if cur.rowcount else None


def source_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["countries"] = d.get("countries") or "[]"
    return d


def employer_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM employers")}

