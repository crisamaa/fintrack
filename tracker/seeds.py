"""Upsert the hand-maintained seed files into the database on every run.

firms/firms.yaml      firms with a known source (unchanged format; new optional fields: sectors, website, priority,
                      enabled). Editing this file is still the way to add or fix a firm by hand.
firms/blocked.yaml    firms that refuse automated visits: registered as employers with an access note, no source.
firms/candidates.yaml names (with an official website when known) for discovery to research: e.g. Peruvian firms.
firms/unresolved.yaml FINMA names that had no readable board in v1: queued as discovery candidates.
Nothing is ever deleted here: a firm removed from the file keeps its history in the database.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tracker import entities
from tracker.connectors import SourceRef, get
from tracker.context import Ctx
from tracker.db import one, tx
from tracker.firms import Firm, load_firms, load_names
from tracker.names import make_id

BLOCKED_NOTE = "Refuses automated visits: check this firm's careers site by hand."


def firm_source_ref(firm: Firm) -> SourceRef:
    if firm.source == "page":
        return SourceRef("page", firm.careers_url)
    slug = firm.slug or ""
    urls = {
        "greenhouse": lambda: f"https://boards.greenhouse.io/{slug}",
        "lever": lambda: f"https://jobs.lever.co/{slug}",
        "ashby": lambda: f"https://jobs.ashbyhq.com/{slug}",
        "workday": lambda: f"https://{firm.site}/{slug.split('/', 1)[1]}",
        "smartrecruiters": lambda: f"https://jobs.smartrecruiters.com/{slug}",
        "recruitee": lambda: f"https://{slug}.recruitee.com",
        "personio": lambda: f"https://{slug}.jobs.personio.{firm.site or 'de'}",
    }
    get(firm.source)  # unknown kinds fail loudly
    site = firm.site if firm.source in ("workday", "personio") else None
    return SourceRef(firm.source, urls[firm.source](), firm.slug, site)


def sync(ctx: Ctx, firms_dir: Path) -> dict[str, int]:
    firms_dir = Path(firms_dir)
    counts = {"firms": 0, "sources_new": 0, "blocked": 0, "candidates": 0}
    with tx(ctx.conn):
        for firm in load_firms(firms_dir / "firms.yaml"):
            entities.register_employer(ctx.conn, ctx.now, name=firm.name, employer_id=firm.id, discovered_via="seed",
                                       website=firm.website, country=firm.country, sectors=firm.all_sectors(),
                                       priority=firm.priority)
            ref = firm_source_ref(firm)
            sid, created = entities.register_source(ctx.conn, ctx.now, ref, employer_id=firm.id,
                                                    discovery_method="seed", evidence="firms/firms.yaml",
                                                    priority=firm.priority, enabled=firm.enabled)
            if not created:
                ctx.conn.execute("UPDATE sources SET enabled = ?, priority = ?, health = CASE WHEN ? = 0 THEN "
                                 "'disabled' WHEN health = 'disabled' THEN 'unknown' ELSE health END WHERE id = ?",
                                 (int(firm.enabled), firm.priority, int(firm.enabled), sid))
            counts["firms"] += 1
            counts["sources_new"] += created
        for name in load_names(firms_dir / "blocked.yaml"):
            eid = entities.resolve(ctx.conn, name)
            if eid is None:
                eid = make_id(name)
                if one(ctx.conn, "SELECT 1 FROM employers WHERE id = ?", (eid,)):
                    eid = None
            if eid is None or not one(ctx.conn, "SELECT 1 FROM employers WHERE id = ?", (eid,)):
                eid = entities.register_employer(ctx.conn, ctx.now, name=name, employer_id=eid, discovered_via="seed",
                                                 access_note=BLOCKED_NOTE)
            ctx.conn.execute("UPDATE employers SET access_note = ? WHERE id = ?", (BLOCKED_NOTE, eid))
            counts["blocked"] += 1
        cand_path = firms_dir / "candidates.yaml"
        items = yaml.safe_load(cand_path.read_text()) if cand_path.exists() else []
        for item in items or []:
            if entities.add_candidate(ctx.conn, ctx.now, name=item["name"], origin="seed",
                                      website=item.get("website"), country=item.get("country"),
                                      sector_hint=item.get("sector"), evidence="firms/candidates.yaml"):
                counts["candidates"] += 1
        for name in load_names(firms_dir / "unresolved.yaml"):
            if entities.add_candidate(ctx.conn, ctx.now, name=name, origin="finma", country="CH", sector_hint="banking",
                                      evidence="FINMA list of authorised banks (no board found in v1)"):
                counts["candidates"] += 1
    return counts
