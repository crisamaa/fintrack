"""Recurring internship programs, kept separately from individual vacancies.

A program is an employer's recurring scheme ("Summer internship, investment banking, Switzerland"), identified by
employer + internship type + main sector + country, without the year, so each year's vacancy links to the same
program. Each intake year can carry several windows, one per kind of evidence:
  confirmed    dates printed on an official page (with the quote)                   -> program_page extraction
  observed     derived from our own record of that year's vacancy (first seen / closing or closed date)
  third_party  dates claimed by a non-employer source; shown, never treated as confirmed
  estimated    a range computed from earlier years' confirmed/observed openings; never an exact promise
Estimates are separate rows and never overwrite the other kinds.
"""
from __future__ import annotations

import hashlib
import statistics
from datetime import date, timedelta

from tracker.context import Ctx
from tracker.db import dumps, iso, loads, one, tx
from tracker.taxonomy import INTERNSHIP_TYPES, SECTORS

RECURRING_TYPES = {"summer", "spring", "winter", "off_cycle", "insight", "graduate"}
MIN_RANGE_DAYS = 14


def program_key(internship_type: str, sector: str | None, country: str | None) -> str:
    return f"{internship_type}|{sector or 'general'}|{country or 'any'}"


def program_name(internship_type: str, sector: str | None, country: str | None) -> str:
    parts = [INTERNSHIP_TYPES.get(internship_type, "Internship")]
    if sector:
        parts.append(SECTORS.get(sector, sector))
    if country:
        parts.append(country)
    return " · ".join(parts)


def ensure_program(ctx: Ctx, employer_id: str, internship_type: str, sector: str | None, country: str | None, *,
                   discovered_via: str, official_url: str | None = None, name: str | None = None) -> str:
    key = program_key(internship_type, sector, country)
    row = one(ctx.conn, "SELECT id FROM programs WHERE employer_id = ? AND name_key = ?", (employer_id, key))
    if row:
        if official_url:
            ctx.conn.execute("UPDATE programs SET official_url = COALESCE(official_url, ?) WHERE id = ?",
                             (official_url, row["id"]))
        return row["id"]
    pid = hashlib.sha1(f"{employer_id}|{key}".encode()).hexdigest()[:12]
    ctx.conn.execute(
        "INSERT INTO programs (id, employer_id, name, name_key, program_type, division, countries, official_url, "
        "discovered_via, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, employer_id, name or program_name(internship_type, sector, country), key, internship_type,
         SECTORS.get(sector) if sector else None, dumps([country] if country else []), official_url, discovered_via,
         iso(ctx.now), iso(ctx.now)))
    ctx.stats["programs_new"] += 1
    return pid


def link_opportunity(ctx: Ctx, opp_id: str) -> str | None:
    """Attach a recurring-type vacancy to its program and refresh that year's observed window."""
    o = one(ctx.conn, "SELECT * FROM opportunities WHERE id = ?", (opp_id,))
    if o["internship_type"] not in RECURRING_TYPES or o["excluded_reason"]:
        return None
    sectors = loads(o["sectors"], [])
    countries = loads(o["countries"], [])
    pid = ensure_program(ctx, o["employer_id"], o["internship_type"], sectors[0] if sectors else None,
                         countries[0] if countries else None, discovered_via="vacancy")
    if o["program_id"] != pid:
        ctx.conn.execute("UPDATE opportunities SET program_id = ? WHERE id = ?", (pid, opp_id))
    observe(ctx, pid)
    return pid


def _intake_year(o) -> int | None:
    if o["intake_year"]:
        return o["intake_year"]
    seen = date.fromisoformat((o["published_at"] or o["first_seen_at"])[:10])
    # Summer and graduate intakes are recruited the year before; off-cycle/insight intakes vary, so leave unknown.
    if o["internship_type"] in ("summer", "graduate"):
        return seen.year + 1 if seen.month >= 7 else seen.year
    return None


def observe(ctx: Ctx, program_id: str) -> None:
    """Rebuild the 'observed' windows of a program from its linked vacancies."""
    by_year: dict[int, list] = {}
    for o in ctx.conn.execute("SELECT * FROM opportunities WHERE program_id = ?", (program_id,)):
        y = _intake_year(o)
        if y:
            by_year.setdefault(y, []).append(o)
    for year, opps in by_year.items():
        opens = min((o["published_at"] or o["first_seen_at"])[:10] for o in opps)
        closes_stated = [o["closes_on"] for o in opps if o["closes_on"]]
        closed = [o["closed_at"][:10] for o in opps if o["closed_at"]]
        closes = max(closes_stated) if closes_stated else (max(closed) if closed and len(closed) == len(opps) else None)
        stated = any(o["published_at"] for o in opps)
        evidence = (f"{len(opps)} vacanc{'y' if len(opps) == 1 else 'ies'}; opening = "
                    f"{'publication date stated by the source' if stated else 'first seen by the tracker'}"
                    f"{'; closing = stated deadline' if closes_stated else '; closing = last day listed' if closes else ''}")
        ctx.conn.execute(
            "INSERT INTO program_windows (program_id, intake_year, kind, opens_on, closes_on, source_url, evidence, "
            "recorded_at) VALUES (?, ?, 'observed', ?, ?, ?, ?, ?) ON CONFLICT(program_id, intake_year, kind) DO "
            "UPDATE SET opens_on = excluded.opens_on, closes_on = excluded.closes_on, evidence = excluded.evidence, "
            "source_url = excluded.source_url, recorded_at = excluded.recorded_at",
            (program_id, year, opens, closes, opps[0]["canonical_url"], evidence, iso(ctx.now)))


def record_window(ctx: Ctx, program_id: str, intake_year: int, kind: str, *, opens_on: str | None,
                  closes_on: str | None, source_url: str, evidence: str) -> None:
    """Confirmed or third-party dates from a page. Never downgrades: a confirmed row is not replaced by third_party."""
    if kind not in ("confirmed", "third_party"):
        raise ValueError(kind)
    ctx.conn.execute(
        "INSERT INTO program_windows (program_id, intake_year, kind, opens_on, closes_on, source_url, evidence, "
        "recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(program_id, intake_year, kind) DO UPDATE SET "
        "opens_on = COALESCE(excluded.opens_on, opens_on), closes_on = COALESCE(excluded.closes_on, closes_on), "
        "source_url = excluded.source_url, evidence = excluded.evidence, recorded_at = excluded.recorded_at",
        (program_id, intake_year, kind, opens_on, closes_on, source_url, evidence, iso(ctx.now)))


def estimate_all(ctx: Ctx) -> int:
    """Refresh 'estimated' windows for the next unrecorded intake year of every program with history."""
    n = 0
    with tx(ctx.conn):
        for p in ctx.conn.execute("SELECT id FROM programs WHERE status != 'discontinued'").fetchall():
            n += estimate(ctx, p["id"])
    return n


def estimate(ctx: Ctx, program_id: str) -> int:
    rows = ctx.conn.execute("SELECT intake_year, kind, opens_on FROM program_windows WHERE program_id = ? AND kind IN "
                            "('confirmed', 'observed') AND opens_on IS NOT NULL", (program_id,)).fetchall()
    per_year: dict[int, date] = {}
    for r in sorted(rows, key=lambda r: r["kind"] != "confirmed"):  # confirmed wins over observed
        per_year.setdefault(r["intake_year"], date.fromisoformat(r["opens_on"]))
    if not per_year:
        return 0
    # Offset of each opening from 1 January of its intake year (negative: recruited the year before).
    offsets = [(d - date(y, 1, 1)).days for y, d in per_year.items()]
    centre = statistics.median(offsets)
    spread = max(MIN_RANGE_DAYS, (max(offsets) - min(offsets)) // 2 + 7)
    year = max(per_year) + 1
    while date(year, 1, 1) + timedelta(days=centre + spread) < ctx.today:
        year += 1  # the next window has already passed unobserved: estimate the one after
    known = one(ctx.conn, "SELECT 1 FROM program_windows WHERE program_id = ? AND intake_year = ? AND kind IN "
                "('confirmed', 'observed')", (program_id, year))
    ctx.conn.execute("DELETE FROM program_windows WHERE program_id = ? AND kind = 'estimated' AND intake_year != ?",
                     (program_id, year))
    if known:
        ctx.conn.execute("DELETE FROM program_windows WHERE program_id = ? AND kind = 'estimated'", (program_id,))
        return 0
    base = date(year, 1, 1)
    years = ", ".join(str(y) for y in sorted(per_year))
    ctx.conn.execute(
        "INSERT INTO program_windows (program_id, intake_year, kind, opens_on, opens_earliest, opens_latest, "
        "evidence, recorded_at) VALUES (?, ?, 'estimated', NULL, ?, ?, ?, ?) ON CONFLICT(program_id, intake_year, "
        "kind) DO UPDATE SET opens_earliest = excluded.opens_earliest, opens_latest = excluded.opens_latest, "
        "evidence = excluded.evidence, recorded_at = excluded.recorded_at",
        (program_id, year, (base + timedelta(days=centre - spread)).isoformat(),
         (base + timedelta(days=centre + spread)).isoformat(),
         f"Estimate from the {years} intake{'s' if len(per_year) > 1 else ''}; not an announced date", iso(ctx.now)))
    return 1
