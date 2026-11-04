"""Merging one firm's fresh postings into the stored roles."""
from datetime import date
from typing import Literal

from pydantic import BaseModel

from tracker.models import Enrichment, Firm, Posting, Role, role_id

ZERO_RESULTS_TO_CLOSE = 3


class FirmResult(BaseModel):
    firm_id: str
    status: Literal["ok", "failed", "unchanged"]
    postings: list[Posting]


class FirmState(BaseModel):
    hash: str | None = None
    last_checked: date | None = None
    zero_streak: int = 0
    # Role ids judged not worth a place on the site; never enriched again.
    rejected: list[str] = []
    # Consecutive unusable-reply / 4xx failures per role id (dropped on success).
    role_failures: dict[str, int] = {}
    # Role ids given up after repeated failures, with the date they were given up; retried later.
    given_up: dict[str, date] = {}
    # Consecutive AI failures reading one page hash.
    fail_hash: str | None = None
    fail_count: int = 0
    # Page hash given up on after repeated failures; not read again until the page changes.
    gave_up_hash: str | None = None


def _close(role: Role, today: date) -> Role:
    return role.model_copy(update={"status": "closed", "closed_on": today})


def merge(
    roles: dict[str, Role],
    result: FirmResult,
    firm: Firm,
    state: FirmState,
    enrichments: dict[str, Enrichment],
    today: date,
) -> tuple[dict[str, Role], FirmState]:
    """Return updated copies of roles and state. A failed firm changes nothing."""
    if result.status == "failed":
        return dict(roles), state.model_copy()
    out = dict(roles)
    new_state = state.model_copy(update={"last_checked": today})
    mine = [rid for rid, r in out.items() if r.firm_id == firm.id]

    if result.status == "unchanged":
        # After an empty read the page hash is kept, so each unchanged run is another empty run.
        if state.zero_streak > 0:
            new_state.zero_streak = state.zero_streak + 1
        for rid in mine:
            if out[rid].status != "open":
                continue
            if new_state.zero_streak >= ZERO_RESULTS_TO_CLOSE:
                out[rid] = _close(out[rid], today)
            else:
                out[rid] = out[rid].model_copy(update={"last_checked": today})
        return out, new_state

    seen: set[str] = set()
    for p in result.postings:
        rid = role_id(firm.id, p.url)
        seen.add(rid)
        existing = out.get(rid)
        if existing is not None:
            update = {"title": p.title, "last_seen": today, "last_checked": today}
            if existing.closes is None or existing.closes >= today:
                update.update(status="open", closed_on=None)  # a role past its closing date stays closed
            out[rid] = existing.model_copy(update=update)
        elif rid in enrichments:
            e = enrichments[rid]
            out[rid] = Role(
                id=rid, firm_id=firm.id, firm_name=firm.name, sector=firm.sector, title=p.title, url=p.url,
                city=e.city, countries=e.countries, season=e.season, year=e.year, closes=e.closes,
                languages_required=e.languages_required, status="open",
                first_seen=today, last_seen=today, last_checked=today,
            )

    if result.postings:
        new_state.zero_streak = 0
        to_close = [rid for rid in mine if rid not in seen]
    else:
        new_state.zero_streak = state.zero_streak + 1
        to_close = mine if new_state.zero_streak >= ZERO_RESULTS_TO_CLOSE else []
    for rid in to_close:
        if out[rid].status == "open":
            out[rid] = _close(out[rid], today)
    for rid in mine:
        if rid not in seen and out[rid].status == "open":
            out[rid] = out[rid].model_copy(update={"last_checked": today})
    return out, new_state


def close_expired(roles: dict[str, Role], today: date) -> dict[str, Role]:
    """Close open roles whose stated closing date is before today."""
    return {
        rid: _close(r, today) if r.status == "open" and r.closes is not None and r.closes < today else r
        for rid, r in roles.items()
    }
