"""When each source is checked next.

Starting heuristics (all adjustable in one place, here):
  priority-1 sources, or sources with open internships               every 8 hours
  normal sources                                                     every 24 hours
  low-activity sources (no internship seen in 60+ days of checking)  every 72 hours
  page sources that need AI to read are not checked more often than daily
  a program at this employer is expected to open soon (estimate)     every 6 hours
  after failures: exponential backoff (2h, 4h, ... capped at 72h); blocked/gone sources weekly
Deterministic jitter (±10%, from the source id and day) spreads checks out so hosts are not hit in bursts.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta

from tracker.db import iso, one, parse_iso

HIGH_H, NORMAL_H, LOW_H, SEASON_H = 8.0, 24.0, 72.0, 6.0
FAIL_BASE_H, FAIL_CAP_H, DEAD_H = 2.0, 72.0, 168.0
LOW_ACTIVITY_DAYS = 60
SEASON_LEAD_DAYS = 21


def _jitter(source_id: str, now: datetime) -> float:
    h = int(hashlib.sha1(f"{source_id}:{now.date()}".encode()).hexdigest()[:4], 16) / 0xFFFF
    return 0.9 + 0.2 * h


def in_season(conn: sqlite3.Connection, employer_id: str | None, now: datetime) -> bool:
    """True when an estimated or confirmed program opening at this employer is near."""
    if not employer_id:
        return False
    today = now.date()
    lo, hi = iso(now - timedelta(days=14))[:10], (today + timedelta(days=SEASON_LEAD_DAYS)).isoformat()
    row = one(conn, "SELECT 1 FROM program_windows w JOIN programs p ON p.id = w.program_id WHERE p.employer_id = ? "
              "AND ((w.kind = 'estimated' AND COALESCE(w.opens_latest, w.opens_on) >= ? AND "
              "COALESCE(w.opens_earliest, w.opens_on) <= ?) OR (w.kind = 'confirmed' AND w.opens_on BETWEEN ? AND ?))",
              (employer_id, lo, hi, lo, hi))
    return row is not None


def interval_hours(conn: sqlite3.Connection, source: dict, now: datetime) -> float:
    if source.get("interval_hours"):
        return float(source["interval_hours"])
    open_here = one(conn, "SELECT 1 FROM source_postings sp JOIN opportunities o ON o.id = sp.opportunity_id "
                    "WHERE sp.source_id = ? AND sp.present = 1 AND o.status = 'open'", (source["id"],))
    hours = NORMAL_H
    if source.get("priority") == 1 or open_here:
        hours = HIGH_H
    created = parse_iso(source.get("created_at"))
    if (not open_here and source.get("postings_total", 0) == 0 and created
            and (now - created).days >= LOW_ACTIVITY_DAYS):
        hours = LOW_H
    if source.get("priority") == 3:
        hours = max(hours, LOW_H)
    if in_season(conn, source.get("employer_id"), now):
        hours = min(hours, SEASON_H)
    if source.get("kind") == "page":
        hours = max(hours, NORMAL_H) if not in_season(conn, source.get("employer_id"), now) else hours
    return hours


def next_after_success(conn: sqlite3.Connection, source: dict, now: datetime) -> str:
    return iso(now + timedelta(hours=interval_hours(conn, source, now) * _jitter(source["id"], now)))


def next_after_failure(source: dict, failures: int, kind: str, now: datetime) -> str:
    if kind in ("blocked", "robots", "auth_required", "unsafe_url") or (kind == "gone" and failures >= 3):
        hours = DEAD_H
    elif kind == "rate_limited":
        hours = max(6.0, FAIL_BASE_H * 2 ** failures)
    else:
        hours = FAIL_BASE_H * 2 ** max(0, failures - 1)
    cap = DEAD_H if hours >= DEAD_H else FAIL_CAP_H
    return iso(now + timedelta(hours=min(hours, cap) * _jitter(source["id"], now)))


def health_after_failure(kind: str, failures: int) -> str:
    if kind in ("blocked", "robots", "auth_required"):
        return "blocked"
    if kind == "gone" and failures >= 3:
        return "gone"
    return "failing" if failures >= 3 else "degraded"


def due_sources(conn: sqlite3.Connection, now: datetime, limit: int) -> list[sqlite3.Row]:
    """Enabled sources whose next check is due: never-checked first, then by priority, then most overdue."""
    return conn.execute(
        "SELECT * FROM sources WHERE enabled = 1 AND access = 'public' AND (next_check_at IS NULL OR next_check_at <= ?) "
        "ORDER BY (last_attempt_at IS NOT NULL), priority, next_check_at LIMIT ?", (iso(now), limit)).fetchall()
