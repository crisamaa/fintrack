"""Market notifications and the daily digest.

Notifications are deduplicated by key, so several sources reporting the same vacancy produce one notification.
Personal reminders (your application deadlines, follow-ups) are computed in the browser from your private data and
shown in the same "Today" panel; they never pass through here.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import httpx

from tracker.context import Ctx
from tracker.db import iso, loads, one

log = logging.getLogger(__name__)


def _add(ctx: Ctx, key: str, kind: str, priority: str, title: str, body: str = "", **refs) -> bool:
    cur = ctx.conn.execute(
        "INSERT OR IGNORE INTO notifications (key, kind, priority, title, body, opportunity_id, employer_id, "
        "program_id, source_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, kind, priority, title, body, refs.get("opportunity_id"), refs.get("employer_id"),
         refs.get("program_id"), refs.get("source_id"), iso(ctx.now)))
    return bool(cur.rowcount)


def _wanted(ctx: Ctx, countries: list[str], sectors: list[str]) -> bool:
    c, s = ctx.config.notify_countries, ctx.config.notify_sectors
    if c and countries and not set(c) & set(countries):
        return False
    if s and sectors and not set(s) & set(sectors):
        return False
    return True


def new_opportunity(ctx: Ctx, opp_id: str) -> None:
    o = one(ctx.conn, "SELECT o.*, e.name employer FROM opportunities o JOIN employers e ON e.id = o.employer_id "
            "WHERE o.id = ?", (opp_id,))
    if o["excluded_reason"] or not _wanted(ctx, loads(o["countries"], []), loads(o["sectors"], [])):
        return
    soon = o["closes_on"] and o["closes_on"] <= (ctx.today + timedelta(days=7)).isoformat()
    verified = o["status"] == "open"
    _add(ctx, f"new:{opp_id}", "new_opportunity", "urgent" if soon else "normal",
         f"{o['employer']}: {o['title']}",
         ("Closes " + o["closes_on"] + ". " if o["closes_on"] else "")
         + ("Listed on the employer's own site." if verified else "Found by search; not yet confirmed on the "
                                                                   "employer's site."),
         opportunity_id=opp_id, employer_id=o["employer_id"])


def source_problem(ctx: Ctx, source: dict, health: str, error: str) -> None:
    emp = one(ctx.conn, "SELECT name, priority FROM employers WHERE id = ?", (source.get("employer_id"),)) \
        if source.get("employer_id") else None
    important = source.get("priority") == 1 or (emp and emp["priority"] == 1)
    if not important:
        return
    _add(ctx, f"source:{source['id']}:{health}:{ctx.today.isoformat()}", "source_problem", "normal",
         f"Cannot check {emp['name'] if emp else source['id']} ({health})", error[:300], source_id=source["id"],
         employer_id=source.get("employer_id"))


def program_openings(ctx: Ctx, days: int = 21) -> int:
    """Programs with a confirmed opening, or an estimated range starting, within `days`."""
    n = 0
    horizon = (ctx.today + timedelta(days=days)).isoformat()
    today = ctx.today.isoformat()
    for w in ctx.conn.execute(
            "SELECT w.*, p.name, p.employer_id, e.name employer FROM program_windows w JOIN programs p ON "
            "p.id = w.program_id JOIN employers e ON e.id = p.employer_id WHERE (w.kind = 'confirmed' AND w.opens_on "
            "BETWEEN ? AND ?) OR (w.kind = 'estimated' AND w.opens_earliest BETWEEN ? AND ?)",
            (today, horizon, today, horizon)).fetchall():
        when = w["opens_on"] if w["kind"] == "confirmed" else f"between {w['opens_earliest']} and {w['opens_latest']}"
        label = "opens" if w["kind"] == "confirmed" else "may open (estimate)"
        n += _add(ctx, f"program:{w['program_id']}:{w['intake_year']}:{w['kind']}", "program_opening",
                  "normal" if w["kind"] == "confirmed" else "info",
                  f"{w['employer']}: {w['name']} {w['intake_year']} {label} {when}", w["evidence"],
                  program_id=w["program_id"], employer_id=w["employer_id"])
    return n


def new_employer(ctx: Ctx, employer_id: str, name: str, how: str) -> None:
    _add(ctx, f"employer:{employer_id}", "new_employer", "info", f"New firm tracked: {name}", how,
         employer_id=employer_id)


def digest(ctx: Ctx) -> dict:
    """Undigested notifications grouped by priority; marks them digested."""
    rows = ctx.conn.execute("SELECT * FROM notifications WHERE digested_at IS NULL ORDER BY "
                            "CASE priority WHEN 'urgent' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, created_at").fetchall()
    items = [{k: r[k] for k in ("kind", "priority", "title", "body", "opportunity_id", "employer_id", "program_id",
                                "source_id", "created_at")} for r in rows]
    ctx.conn.execute("UPDATE notifications SET digested_at = ? WHERE digested_at IS NULL", (iso(ctx.now),))
    return {"generated_at": iso(ctx.now), "items": items}


def post_github_issue(ctx: Ctx, digest_data: dict, site_url: str | None = None) -> bool:
    """Open the digest as an issue in this repository (opt-in: notifications.github_issue / DIGEST_ISSUE)."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    items = digest_data["items"]
    if not (ctx.config.digest_issue and token and repo and items):
        return False
    lines = [f"- **{i['priority']}** · {i['title']}" + (f" — {i['body']}" if i["body"] else "") for i in items[:60]]
    if site_url:
        lines.append(f"\nOpen the tracker: {site_url}")
    try:
        r = httpx.post(f"https://api.github.com/repos/{repo}/issues", timeout=20,
                       headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                       json={"title": f"Fintrack digest {ctx.today.isoformat()} ({len(items)} items)",
                             "body": "\n".join(lines), "labels": ["digest"]})
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.error("could not post the digest issue: %s", e)
        ctx.errors.append(f"digest issue: {e}")
        return False
