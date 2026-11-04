from datetime import timedelta

from tracker import entities
from tracker.connectors import FetchResult, RawPosting, SourceRef
from tracker.db import one


def employer(ctx, eid="acme", name="Acme Bank", website="https://www.acmebank.ch", country="CH", priority=2):
    return entities.register_employer(ctx.conn, ctx.now, name=name, employer_id=eid, discovered_via="seed",
                                      website=website, country=country, sectors=["banking"], priority=priority)


def source(ctx, eid="acme", kind="greenhouse", board="acme", url=None, **kw):
    ref = SourceRef(kind, url or f"https://boards.greenhouse.io/{board}", board if kind != "page" else None)
    sid, _ = entities.register_source(ctx.conn, ctx.now, ref, employer_id=eid, discovery_method="seed", **kw)
    return sid


def row(ctx, sid):
    return dict(one(ctx.conn, "SELECT * FROM sources WHERE id = ?", (sid,)))


def posting(n=1, title=None, location="Zurich, Switzerland", extraction="structured", **kw):
    kw.setdefault("description", "Summer internship in our M&A team. You must have the right to work in Switzerland. "
                                 "Fluent German and English required.")
    return RawPosting(external_id=str(n), title=title or f"2027 Summer Analyst Internship - M&A {n}",
                      url=f"https://boards.greenhouse.io/acme/jobs/{n}", location=location, extraction=extraction,
                      **kw)


def result(*postings, **kw):
    return FetchResult(list(postings), total_listed=kw.pop("total_listed", len(postings)), **kw)


def opp_for(ctx, n=1, sid="greenhouse:acme"):
    r = one(ctx.conn, "SELECT o.* FROM opportunities o JOIN source_postings sp ON sp.opportunity_id = o.id WHERE "
            "sp.source_id = ? AND sp.external_id = ?", (sid, str(n)))
    return dict(r) if r else None


def later(ctx, **delta):
    ctx.now = ctx.now + timedelta(**delta)
    if ctx.ai:
        ctx.ai.now = ctx.now
        ctx.ai.budget.now = ctx.now
    return ctx
