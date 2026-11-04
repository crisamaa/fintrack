"""The daily run: read every firm, enrich new postings, merge, and write the site data."""
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

import anthropic
import httpx
import yaml

from tracker.ai import enrich, extract_postings
from tracker.budget import Budget
from tracker.feeds import SourceError, fetch_feed
from tracker.fetch import PoliteClient, text_hash
from tracker.merge import FirmResult, FirmState, close_expired, merge
from tracker.models import ALLOWED_COUNTRIES, Firm, Role, role_id

MAX_AI_FAILURES = 3
USER_AGENT = "europe-internship-tracker/1.0"


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    os.replace(tmp, path)


def _load_roles(path: Path) -> dict[str, Role]:
    if not path.exists():
        return {}
    return {rid: Role.model_validate(r) for rid, r in json.loads(path.read_text()).items()}


def _load_state(path: Path) -> dict[str, FirmState]:
    if not path.exists():
        return {}
    return {fid: FirmState.model_validate(s) for fid, s in json.loads(path.read_text()).items()}


def _save(data_dir: Path, roles: dict[str, Role], states: dict[str, FirmState]) -> None:
    _write_json(data_dir / "roles.json", {rid: r.model_dump(mode="json") for rid, r in roles.items()})
    _write_json(data_dir / "firm_state.json", {fid: s.model_dump(mode="json") for fid, s in states.items()})


def _enrich_new(client, budget, firm, postings, roles, state):
    """Enrich postings not seen before. Returns (accepted enrichments, state, all_done)."""
    rejected = set(state.rejected)
    failures = dict(state.role_failures)
    accepted = {}
    all_done = True
    for p in postings:
        rid = role_id(firm.id, p.url)
        if rid in roles or rid in rejected or rid in accepted:
            continue
        e = enrich(client, budget, p)
        if e is None:
            all_done = False
            if budget.exhausted:
                break  # out of money is not the posting's fault; retry tomorrow
            failures[rid] = failures.get(rid, 0) + 1
            if failures[rid] >= MAX_AI_FAILURES:
                rejected.add(rid)
                del failures[rid]
            continue
        failures.pop(rid, None)
        if e.is_internship and ALLOWED_COUNTRIES.intersection(e.countries):
            accepted[rid] = e
        else:
            rejected.add(rid)
    new_state = state.model_copy(update={"rejected": sorted(rejected), "role_failures": failures})
    return accepted, new_state, all_done


def _process(firm, roles, state, client, http, polite, budget, today):
    """Returns (roles, state, checked, failed_listed)."""
    new_hash = None
    if firm.source == "page":
        if not firm.careers_url:
            raise SourceError(f"{firm.id}: page firm without careers_url")
        text = polite.get_text(firm.careers_url)
        h = text_hash(text)
        if h == state.hash:
            result = FirmResult(firm_id=firm.id, status="unchanged", postings=[])
            roles, state = merge(roles, result, firm, state, {}, today)
            return roles, state, True, False
        if not budget.can_spend():
            return roles, state, False, False
        postings = extract_postings(client, budget, firm, text)
        if postings is None:
            if budget.exhausted:
                return roles, state, False, False
            count = state.fail_count + 1 if state.fail_hash == h else 1
            if count >= MAX_AI_FAILURES:
                # Deterministic failure: stop re-reading this page until it changes.
                state = state.model_copy(update={"hash": h, "fail_hash": None, "fail_count": 0})
            else:
                state = state.model_copy(update={"fail_hash": h, "fail_count": count})
            return roles, state, False, True
        state = state.model_copy(update={"fail_hash": None, "fail_count": 0})
        new_hash = h
    else:
        postings = fetch_feed(firm, http)

    enrichments, state, all_done = _enrich_new(client, budget, firm, postings, roles, state)
    result = FirmResult(firm_id=firm.id, status="ok", postings=postings)
    roles, state = merge(roles, result, firm, state, enrichments, today)
    if new_hash is not None and all_done:
        # Keep the old hash while postings are still waiting to be enriched, so the page is read again.
        state = state.model_copy(update={"hash": new_hash})
    return roles, state, True, False


def run(firms: list[Firm], data_dir: Path, site_dir: Path, today: date, client, http,
        polite: PoliteClient | None = None) -> None:
    data_dir, site_dir = Path(data_dir), Path(site_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "data").mkdir(parents=True, exist_ok=True)
    budget = Budget(data_dir / "spend.json", today.strftime("%Y-%m"))
    roles = _load_roles(data_dir / "roles.json")
    states = _load_state(data_dir / "firm_state.json")
    failed: list[str] = []
    checked = 0

    for firm in firms:
        state = states.get(firm.id, FirmState())
        try:
            if firm.source == "page" and polite is None:
                polite = PoliteClient()
            roles, state, ok, listed = _process(firm, roles, state, client, http, polite, budget, today)
        except Exception:
            ok, listed = False, True
        # state may be unchanged after an exception: nothing partial is kept for this firm
        states[firm.id] = state
        checked += ok
        if listed:
            failed.append(firm.name)
        _save(data_dir, roles, states)

    roles = close_expired(roles, today)
    _save(data_dir, roles, states)
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "firms_checked": checked,
        "firms_failed": failed,
        "budget_exhausted": budget.exhausted,
        "roles": [r.model_dump(mode="json") for r in sorted(roles.values(), key=lambda r: (r.firm_id, r.title, r.id))],
    }
    _write_json(site_dir / "data" / "roles.json", out)


def main() -> None:
    raw = yaml.safe_load(Path("firms/firms.yaml").read_text())
    items = raw["firms"] if isinstance(raw, dict) else raw
    firms = [Firm.model_validate(f) for f in items]
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True) as http:
        run(firms, Path("data"), Path("site"), date.today(), anthropic.Anthropic(), http)


if __name__ == "__main__":
    main()
