"""Field-level provenance. Every material field of an opportunity is recorded as a fact with its method, source,
model and evidence; the column on `opportunities` always mirrors the highest-precedence fact.

Precedence: manual > structured (ATS API field) > jsonld > rule (deterministic parsing) > ai. So an AI extraction can
fill a field nothing better has filled, but can never overwrite a value from a more reliable method.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from tracker.db import dumps, iso, loads, one

PRECEDENCE = {"manual": 5, "structured": 4, "jsonld": 3, "rule": 2, "ai": 1}
FIELDS = {"title", "city", "countries", "internship_type", "intake_year", "closes_on", "languages", "sectors",
          "published_at"}
_JSON_FIELDS = {"countries", "languages", "sectors"}
_EMPTY = (None, "", [], "unknown")


def _column(field: str, value):
    return dumps(value) if field in _JSON_FIELDS else value


def set_fact(conn: sqlite3.Connection, now: datetime, opp_id: str, field: str, value, method: str, *,
             source_id: str | None = None, model: str | None = None, prompt_version: str | None = None,
             evidence: str | None = None, record_change: bool = True) -> bool:
    """Record a fact; update the column when this fact now has the highest precedence. Returns True on change."""
    if field not in FIELDS:
        raise ValueError(f"unknown fact field {field!r}")
    if method not in PRECEDENCE:
        raise ValueError(f"unknown method {method!r}")
    if value in _EMPTY:
        return False
    conn.execute(
        "INSERT INTO opportunity_facts (opportunity_id, field, method, value, source_id, model, prompt_version, "
        "evidence, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(opportunity_id, field, method) DO "
        "UPDATE SET value = excluded.value, source_id = excluded.source_id, model = excluded.model, "
        "prompt_version = excluded.prompt_version, evidence = excluded.evidence, recorded_at = excluded.recorded_at",
        (opp_id, field, method, dumps(value), source_id, model, prompt_version, (evidence or "")[:500] or None,
         iso(now)))
    best = winning(conn, opp_id, field)
    new = _column(field, best)
    old = one(conn, f"SELECT {field} FROM opportunities WHERE id = ?", (opp_id,))[0]
    if old == new:
        return False
    conn.execute(f"UPDATE opportunities SET {field} = ?, updated_at = ? WHERE id = ?", (new, iso(now), opp_id))
    if record_change and old not in (None, "", "[]", "unknown"):
        conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, field, old_value, new_value, "
                     "source_id, note) VALUES (?, ?, 'changed', ?, ?, ?, ?, ?)",
                     (opp_id, iso(now), field, str(old), str(new), source_id, f"{method} fact"))
    return True


def winning(conn: sqlite3.Connection, opp_id: str, field: str):
    rows = conn.execute("SELECT method, value FROM opportunity_facts WHERE opportunity_id = ? AND field = ?",
                        (opp_id, field)).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: PRECEDENCE[r["method"]])
    return loads(best["value"])


def facts_for(conn: sqlite3.Connection, opp_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT field, method, value, source_id, model, prompt_version, evidence, recorded_at FROM opportunity_facts "
        "WHERE opportunity_id = ? ORDER BY field, method", (opp_id,))]
