"""Leads the user came across (Channels C and E): a posting or board URL from a university newsletter, a careers
fair or an email. Restricted sources (university portals behind a login, LinkedIn) are never scraped; the user
pastes the public URL here, or into the "Import lead" workflow, and it enters the same pipeline as everything else.
"""
from __future__ import annotations

from datetime import datetime

from tracker import connectors, db, entities, ingest
from tracker.config import Config
from tracker.connectors import SourceRef
from tracker.connectors.page import jsonld_postings, posting_from_jsonld
from tracker.context import Ctx
from tracker.db import one, tx
from tracker.http import SafeClient, SourceFailure, check_url


def import_lead(cfg: Config, url: str, *, employer_id: str | None, now: datetime,
                http: SafeClient | None = None) -> dict:
    check_url(url, None)
    conn = db.load(cfg.db_dir, now, backup_dir=cfg.backup_dir)
    http = http or SafeClient()
    ctx = Ctx(conn=conn, now=now, config=cfg, http=http)
    out: dict = {"url": url}
    if employer_id and not one(conn, "SELECT 1 FROM employers WHERE id = ?", (employer_id,)):
        raise SystemExit(f"unknown employer id {employer_id!r}")
    with tx(conn):
        ref = connectors.detect(url)
        if ref is not None:
            sid, created = entities.register_source(conn, now, ref, employer_id=employer_id, discovery_method="import",
                                                    association="verified" if employer_id else "unverified",
                                                    evidence="imported by the user")
            out.update(source=sid, created=created)
        entities.register_source(conn, now, SourceRef("import", "import://manual", "manual"), employer_id=None,
                                 discovery_method="import", association="unverified", category="import",
                                 enabled=False, evidence="leads imported by the user")
        src = dict(one(conn, "SELECT * FROM sources WHERE id = 'import:manual'"))
        try:
            resp = http.get(url)
        except SourceFailure as e:
            out["fetch_error"] = str(e)
            resp = None
        if resp is not None:
            postings = [p for p in (posting_from_jsonld(n, resp.url) for n in jsonld_postings(resp.text, resp.url))
                        if p]
            for p in postings:
                p.extraction = "import"
                if employer_id and not p.structured.get("hiring_organization"):
                    p.structured["hiring_organization"] = one(conn, "SELECT name FROM employers WHERE id = ?",
                                                              (employer_id,))["name"]
            result = ingest.apply_postings(ctx, src, postings, complete=False)
            out.update(postings=len(postings), new_opportunities=result.new_opportunities)
            if not postings and ref is None:
                out["note"] = ("no structured job posting on this page; if it is a careers page, add it to "
                               "firms/firms.yaml as a page source")
    db.save(conn, cfg.db_dir)
    return out
