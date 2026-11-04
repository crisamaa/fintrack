"""A durable work queue in the database.

Jobs are identified by (kind, key), so enqueueing the same work twice is a no-op; that makes every producer
idempotent. A claimed job holds a lease; a run that dies mid-job leaves the lease to expire and the next run takes
the job back (recover()). Failures retry with exponential backoff and jitter until max_attempts, then the job is
marked dead and stays visible in the Sources view.
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta

from tracker.db import dumps, iso, loads, one

BACKOFF_BASE_HOURS = 1.0
BACKOFF_CAP_HOURS = 48.0
LEASE_MINUTES = 30


def enqueue(conn: sqlite3.Connection, now: datetime, kind: str, key: str, payload: dict | None = None, *,
            delay: timedelta = timedelta(0), max_attempts: int = 5, refresh: bool = False) -> bool:
    """Queue a job. Returns True when a job was created or re-armed.

    refresh=True re-arms a finished or dead job (e.g. the page changed, so extraction must run again).
    """
    existing = one(conn, "SELECT status, payload FROM jobs WHERE kind = ? AND key = ?", (kind, key))
    when = iso(now + delay)
    if existing is None:
        conn.execute("INSERT INTO jobs (kind, key, payload, status, attempts, max_attempts, next_attempt_at, "
                     "created_at, updated_at) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)",
                     (kind, key, dumps(payload or {}), max_attempts, when, iso(now), iso(now)))
        return True
    if existing["status"] in ("done", "dead") and refresh:
        conn.execute("UPDATE jobs SET status = 'pending', attempts = 0, payload = ?, next_attempt_at = ?, "
                     "last_error = NULL, updated_at = ? WHERE kind = ? AND key = ?",
                     (dumps(payload or {}), when, iso(now), kind, key))
        return True
    if existing["status"] == "pending" and payload is not None and loads(existing["payload"]) != payload:
        conn.execute("UPDATE jobs SET payload = ?, updated_at = ? WHERE kind = ? AND key = ?",
                     (dumps(payload), iso(now), kind, key))
    return False


def recover(conn: sqlite3.Connection, now: datetime) -> int:
    """Return jobs whose lease ran out (the run that held them stopped) to the queue."""
    cur = conn.execute("UPDATE jobs SET status = 'pending', lease_until = NULL, last_error = 'interrupted', "
                       "updated_at = ? WHERE status = 'running' AND (lease_until IS NULL OR lease_until < ?)",
                       (iso(now), iso(now)))
    return cur.rowcount


def due(conn: sqlite3.Connection, now: datetime, kinds: list[str], limit: int) -> list[sqlite3.Row]:
    marks = ", ".join("?" * len(kinds))
    return conn.execute(f"SELECT * FROM jobs WHERE status = 'pending' AND next_attempt_at <= ? AND kind IN ({marks}) "
                        "ORDER BY next_attempt_at, id LIMIT ?", (iso(now), *kinds, limit)).fetchall()


def claim(conn: sqlite3.Connection, now: datetime, job_id: int) -> None:
    conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_until = ?, updated_at = ? "
                 "WHERE id = ?", (iso(now + timedelta(minutes=LEASE_MINUTES)), iso(now), job_id))


def done(conn: sqlite3.Connection, now: datetime, job_id: int) -> None:
    conn.execute("UPDATE jobs SET status = 'done', lease_until = NULL, last_error = NULL, updated_at = ? WHERE id = ?",
                 (iso(now), job_id))


def defer(conn: sqlite3.Connection, now: datetime, job_id: int, reason: str, delay: timedelta) -> None:
    """Put a job back without counting an attempt (budget stop, AI outage: not the job's fault)."""
    conn.execute("UPDATE jobs SET status = 'pending', attempts = MAX(attempts - 1, 0), lease_until = NULL, "
                 "last_error = ?, next_attempt_at = ?, updated_at = ? WHERE id = ?",
                 (reason[:500], iso(now + delay), iso(now), job_id))


def backoff(attempts: int, key: str) -> timedelta:
    hours = min(BACKOFF_CAP_HOURS, BACKOFF_BASE_HOURS * 2 ** max(0, attempts - 1))
    jitter = int(hashlib.sha1(f"{key}:{attempts}".encode()).hexdigest()[:4], 16) / 0xFFFF  # 0..1, deterministic
    return timedelta(hours=hours * (0.85 + 0.3 * jitter))


def fail(conn: sqlite3.Connection, now: datetime, job_id: int, error: str) -> str:
    """Record a failed attempt; returns the new status ('pending' with backoff, or 'dead')."""
    row = one(conn, "SELECT attempts, max_attempts, key FROM jobs WHERE id = ?", (job_id,))
    if row["attempts"] >= row["max_attempts"]:
        conn.execute("UPDATE jobs SET status = 'dead', lease_until = NULL, last_error = ?, updated_at = ? WHERE id = ?",
                     (error[:500], iso(now), job_id))
        return "dead"
    conn.execute("UPDATE jobs SET status = 'pending', lease_until = NULL, last_error = ?, next_attempt_at = ?, "
                 "updated_at = ? WHERE id = ?",
                 (error[:500], iso(now + backoff(row["attempts"], row["key"])), iso(now), job_id))
    return "pending"


def summary(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT kind, status, COUNT(*) n FROM jobs GROUP BY kind, status"):
        out.setdefault(r["kind"], {})[r["status"]] = r["n"]
    return out
