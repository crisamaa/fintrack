"""The vacancy lifecycle as an explicit state machine.

    pending      found, not yet confirmed on an employer-controlled source (e.g. from a web search)
    open         confirmed listed by a monitored source
    unavailable  every source for it is failing and it has not been confirmed for a while; NOT closed
    closed       a complete, successful check of its sources no longer lists it (evidence required)
    expired      its stated closing date has passed
    unknown      no longer assessable (e.g. its only source was disabled)

Reopening (closed/expired -> open) is a normal transition and is counted. Every transition writes an
opportunity_events row with its reason, so the history explains every status the site shows.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from tracker.db import iso, one

STATES = ("pending", "open", "unavailable", "closed", "expired", "unknown")
ALLOWED: dict[str, set[str]] = {
    "pending": {"open", "closed", "expired", "unavailable", "unknown"},
    "open": {"unavailable", "closed", "expired", "unknown"},
    "unavailable": {"open", "closed", "expired", "unknown"},
    "closed": {"open", "expired"},
    "expired": {"open", "closed"},
    "unknown": {"open", "closed", "expired", "unavailable"},
}
NEEDS_EVIDENCE = {"closed", "expired"}


class IllegalTransition(Exception):
    pass


def transition(conn: sqlite3.Connection, now: datetime, opp_id: str, to: str, reason: str,
               source_id: str | None = None) -> bool:
    """Move an opportunity to `to`. Returns False when it is already there. Raises on an illegal move."""
    row = one(conn, "SELECT status FROM opportunities WHERE id = ?", (opp_id,))
    if row is None:
        raise KeyError(opp_id)
    frm = row["status"]
    if frm == to:
        return False
    if to not in ALLOWED.get(frm, set()):
        raise IllegalTransition(f"{opp_id}: {frm} -> {to}")
    if to in NEEDS_EVIDENCE and not reason.strip():
        raise IllegalTransition(f"{opp_id}: {to} needs a reason")
    sets = {"status": to, "updated_at": iso(now)}
    if to in ("closed", "expired"):
        sets.update(closed_at=iso(now), close_reason=reason)
    if to == "open":
        sets["last_verified_at"] = iso(now)
        if frm in ("closed", "expired"):
            conn.execute("UPDATE opportunities SET reopened_count = reopened_count + 1 WHERE id = ?", (opp_id,))
        sets.update(closed_at=None, close_reason=None)
    cols = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE opportunities SET {cols} WHERE id = ?", (*sets.values(), opp_id))
    event = "reopened" if to == "open" and frm in ("closed", "expired") else "status"
    conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, from_status, to_status, source_id, note) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (opp_id, iso(now), event, frm, to, source_id, reason))
    return True
