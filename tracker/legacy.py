"""One-time import of the v1 JSON data (data/roles.json, data/firm_state.json) into the database.

Runs once, after the seed sync, and records `legacy_imported` in meta. Role ids are kept as opportunity ids, so
applications tracked in the browser under those ids still resolve. The import verifies itself: every v1 role must
end up as an opportunity (or be mapped to one with the same canonical URL), or nothing is committed.
"""
from __future__ import annotations

import json
from pathlib import Path

from tracker import entities, facts
from tracker.context import Ctx
from tracker.db import audit, dumps, get_meta, iso, one, set_meta, tx
from tracker.identity import canonical_url, title_key
from tracker.taxonomy import LEGACY_SEASONS, LEGACY_SECTORS


class LegacyImportError(Exception):
    pass


def _ts(d: str | None) -> str | None:
    return f"{d}T00:00:00Z" if d and len(d) == 10 else d


def import_v1(ctx: Ctx, data_dir: Path) -> dict | None:
    if get_meta(ctx.conn, "legacy_imported"):
        return None
    data_dir = Path(data_dir)
    roles_path, state_path = data_dir / "roles.json", data_dir / "firm_state.json"
    roles = json.loads(roles_path.read_text()) if roles_path.exists() else {}
    states = json.loads(state_path.read_text()) if state_path.exists() else {}
    report = {"roles": len(roles), "imported": 0, "mapped": {}, "states": 0}
    conn = ctx.conn
    with tx(conn):
        for fid, st in states.items():
            src = one(conn, "SELECT id FROM sources WHERE employer_id = ? AND discovery_method = 'seed'", (fid,))
            if src is None:
                continue
            last = _ts(st.get("last_checked"))
            conn.execute("UPDATE sources SET last_attempt_at = ?, last_success_at = ?, content_hash = ?, "
                         "zero_streak = ? WHERE id = ?",
                         (last, last, st.get("hash"), int(st.get("zero_streak") or 0), src["id"]))
            report["states"] += 1
        for rid, r in roles.items():
            fid = r["firm_id"]
            if not one(conn, "SELECT 1 FROM employers WHERE id = ?", (fid,)):
                entities.register_employer(conn, ctx.now, name=r["firm_name"], employer_id=fid, discovered_via="seed",
                                           sectors=LEGACY_SECTORS.get(r.get("sector"), []), evidence="v1 roles.json")
            canon = canonical_url(r["url"])
            dup = one(conn, "SELECT id FROM opportunities WHERE employer_id = ? AND canonical_url = ?", (fid, canon))
            if dup:
                report["mapped"][rid] = dup["id"]
                continue
            status = "open" if r["status"] == "open" else "closed"
            conn.execute(
                "INSERT INTO opportunities (id, employer_id, title, canonical_url, title_key, internship_type, status, "
                "enrichment, discovered_via, first_seen_at, last_seen_at, last_verified_at, closed_at, close_reason, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'unknown', ?, 'done', 'legacy', ?, ?, ?, ?, ?, ?, ?)",
                (rid, fid, r["title"], canon, title_key(r["title"]), status, _ts(r["first_seen"]), _ts(r["last_seen"]),
                 _ts(r["last_checked"]), _ts(r.get("closed_on")),
                 "closed in v1" if status == "closed" else None, iso(ctx.now), iso(ctx.now)))
            src = one(conn, "SELECT id, kind FROM sources WHERE employer_id = ? AND discovery_method = 'seed'", (fid,))
            if src:
                conn.execute("INSERT OR IGNORE INTO source_postings (source_id, external_id, opportunity_id, title, url, "
                             "content_hash, extraction, present, first_seen_at, last_seen_at) VALUES "
                             "(?, ?, ?, ?, ?, 'legacy', ?, ?, ?, ?)",
                             (src["id"], r["url"], rid, r["title"], r["url"],
                              "ai" if src["kind"] == "page" else "structured", int(status == "open"),
                              _ts(r["first_seen"]), _ts(r["last_seen"])))
            sid = src["id"] if src else None
            # v1 filled these with the AI enrichment step, so they are recorded as AI facts.
            for field, value in (("title", r["title"]), ("city", r.get("city")), ("countries", r.get("countries")),
                                 ("internship_type", LEGACY_SEASONS.get(r.get("season") or "unknown", "unknown")),
                                 ("intake_year", r.get("year")), ("closes_on", r.get("closes")),
                                 ("languages", r.get("languages_required"))):
                facts.set_fact(conn, ctx.now, rid, field, value, "ai", source_id=sid, prompt_version="v1",
                               record_change=False)
            facts.set_fact(conn, ctx.now, rid, "sectors", LEGACY_SECTORS.get(r.get("sector"), []), "rule",
                           source_id=sid, record_change=False)
            conn.execute("INSERT INTO opportunity_events (opportunity_id, at, event, to_status, note) VALUES "
                         "(?, ?, 'discovered', ?, 'imported from v1')", (rid, _ts(r["first_seen"]), status))
            report["imported"] += 1
        # integrity: every v1 role is an opportunity or mapped to one
        for rid in roles:
            target = report["mapped"].get(rid, rid)
            if not one(conn, "SELECT 1 FROM opportunities WHERE id = ?", (target,)):
                raise LegacyImportError(f"v1 role {rid} did not survive the import")
        if report["imported"] + len(report["mapped"]) != len(roles):
            raise LegacyImportError(f"imported {report['imported']} + mapped {len(report['mapped'])} != {len(roles)}")
        set_meta(conn, "legacy_imported", dumps({"at": iso(ctx.now), **report}))
        set_meta(conn, "legacy_id_map", dumps(report["mapped"]))
        audit(conn, ctx.now, "legacy_import", "database", None, report, actor="migration")
    return report
