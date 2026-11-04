"""The daily run: read every firm, enrich new postings, merge, and write the site data."""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import yaml

from tracker.ai import AIUnusable, APIError, GeminiClient, enrich, extract_postings
from tracker.budget import MONTHLY_CAP_USD, Budget, cap_from_env
from tracker.feeds import SourceError, fetch_feed
from tracker.fetch import PoliteClient, text_hash
from tracker.firms import load_firms
from tracker.merge import FirmResult, FirmState, close_expired, merge
from tracker.models import ALLOWED_COUNTRIES, Firm, Role, role_id

MAX_AI_FAILURES = 3
GIVE_UP_RETRY_DAYS = 30
DEADLINE_MINUTES = 45  # stop starting new firms after this long, so the site data is still written
log = logging.getLogger(__name__)
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


class RunFailed(Exception):
    """The run finished and saved its data, but should exit non-zero (all firms failed or bad API key)."""


@dataclass
class Outcome:
    read: bool = False      # the firm was read (or confirmed unchanged)
    listed: bool = False    # goes in firms_failed
    errors: list = field(default_factory=list)


def _is_account_error(exc: BaseException) -> bool:
    """Client errors that are about the account or request setup, not the posting: never a strike."""
    if isinstance(exc, APIError) and not _is_strike(exc):
        return True
    return False


def _is_strike(exc: BaseException) -> bool:
    """Billed-but-unusable replies and input-specific 400/413 count toward giving up; nothing else does."""
    if isinstance(exc, AIUnusable):
        return True
    if isinstance(exc, APIError) and exc.status_code in (400, 413):
        return not any(w in str(exc).lower() for w in ("api key", "billing", "credit balance"))
    return False


def _enrich_new(client, budget, firm, postings, roles, state, today, blocked=False):
    """Enrich postings not seen before.

    Returns (accepted enrichments, state, all_done, transient, errors). Strike errors are counted against the
    role id and collected so the firm's progress is still merged; an auth error stops the loop.
    """
    rejected = set(state.rejected)
    failures = dict(state.role_failures)
    present = {role_id(firm.id, p.url) for p in postings}
    given_up = {rid: d for rid, d in state.given_up.items() if rid in present}
    accepted = {}
    all_done, transient, errors = True, False, []
    for p in postings:
        rid = role_id(firm.id, p.url)
        if rid in roles or rid in rejected or rid in accepted:
            continue
        if rid in given_up:
            if (today - given_up[rid]).days < GIVE_UP_RETRY_DAYS:
                continue
        if blocked:  # an account error earlier in this run: no more AI calls
            all_done, transient = False, True
            break
        try:
            e = enrich(client, budget, p)
        except Exception as exc:
            if _is_account_error(exc):
                log.error("firm %s: AI account error", firm.id, exc_info=exc)
                errors.append(exc)
                all_done = False
                break  # no strike, no state change for this role
            if not _is_strike(exc):
                raise
            log.warning("firm %s: AI error on %s", firm.id, p.url, exc_info=exc)
            errors.append(exc)
            given_up.pop(rid, None)  # a retry that produced a strike starts counting afresh
            failures[rid] = failures.get(rid, 0) + 1
            if failures[rid] >= MAX_AI_FAILURES:
                given_up[rid] = today
                del failures[rid]
            else:
                all_done = False  # still pending: keep re-reading until it succeeds or is given up
            continue
        if e is None:
            all_done = False
            if budget.exhausted:
                break  # out of money is not the posting's fault; retry tomorrow
            transient = True
            continue
        failures.pop(rid, None)
        given_up.pop(rid, None)
        if e.is_internship and ALLOWED_COUNTRIES.intersection(e.countries):
            accepted[rid] = e
        else:
            rejected.add(rid)
    failures = {rid: n for rid, n in failures.items() if rid in present}
    new_state = state.model_copy(update={"rejected": sorted(rejected), "role_failures": failures,
                                         "given_up": given_up})
    return accepted, new_state, all_done, transient, errors


def _process(firm, roles, state, client, http, polite, budget, today, blocked=False):
    """Returns (roles, state, Outcome)."""
    new_hash = None
    if firm.source == "page":
        if not firm.careers_url:
            raise SourceError(f"{firm.id}: page firm without careers_url")
        text = polite.get_text(firm.careers_url)
        h = text_hash(text)
        if h == state.gave_up_hash:
            return roles, state, Outcome(listed=True)  # still failing on this exact page: no AI call
        retry_due = any((today - d).days >= GIVE_UP_RETRY_DAYS for d in state.given_up.values())
        if h == state.hash and not retry_due and not state.role_failures:
            result = FirmResult(firm_id=firm.id, status="unchanged", postings=[])
            roles, state = merge(roles, result, firm, state, {}, today)
            return roles, state, Outcome(read=True, listed=bool(state.given_up))
        if blocked or not budget.can_spend():
            return roles, state, Outcome()
        before = state
        update = {"gave_up_hash": None}
        if h != state.hash:
            update["given_up"] = {}  # the page changed: give given-up postings another chance
        state = state.model_copy(update=update)
        try:
            postings = extract_postings(client, budget, firm, text)
        except Exception as exc:
            if _is_account_error(exc):
                log.error("firm %s: AI account error reading page", firm.id, exc_info=exc)
                return roles, before, Outcome(listed=True, errors=[exc])
            if not _is_strike(exc):
                raise
            log.warning("firm %s: AI error reading page", firm.id, exc_info=exc)
            count = state.fail_count + 1 if state.fail_hash == h else 1
            if count >= MAX_AI_FAILURES:
                state = state.model_copy(update={"gave_up_hash": h, "fail_hash": None, "fail_count": 0})
            else:
                state = state.model_copy(update={"fail_hash": h, "fail_count": count})
            return roles, state, Outcome(listed=True, errors=[exc])
        if postings is None:  # budget stop or transient trouble: not a strike
            return roles, state, Outcome(listed=not budget.exhausted)
        state = state.model_copy(update={"fail_hash": None, "fail_count": 0})
        new_hash = h
    else:
        postings = fetch_feed(firm, http)

    enrichments, state, all_done, transient, errors = _enrich_new(
        client, budget, firm, postings, roles, state, today, blocked)
    result = FirmResult(firm_id=firm.id, status="ok", postings=postings)
    roles, state = merge(roles, result, firm, state, enrichments, today)
    if new_hash is not None and all_done:
        # Keep the old hash while postings are still waiting to be enriched, so the page is read again.
        state = state.model_copy(update={"hash": new_hash})
    listed = bool(errors) or transient or bool(state.given_up)
    return roles, state, Outcome(read=True, listed=listed, errors=errors)


def run(firms: list[Firm], data_dir: Path, site_dir: Path, today: date, client, http,
        polite: PoliteClient | None = None, not_covered: list[str] | None = None,
        deadline_minutes: float = DEADLINE_MINUTES, clock=time.monotonic,
        cap_usd: float | None = MONTHLY_CAP_USD) -> None:
    data_dir, site_dir = Path(data_dir), Path(site_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "data").mkdir(parents=True, exist_ok=True)
    budget = Budget(data_dir / "spend.json", today.strftime("%Y-%m"), cap_usd)
    roles = _load_roles(data_dir / "roles.json")
    states = _load_state(data_dir / "firm_state.json")
    failed: list[str] = []
    checked = unread = 0
    account_error = False

    started = clock()
    # Never-checked firms first, then the least recently checked; ties keep the firm-list order (sort is stable).
    def _age(f):
        lc = states.get(f.id, FirmState()).last_checked
        return (lc is not None, lc or date.min)

    order = sorted(firms, key=_age)
    for i, firm in enumerate(order):
        if clock() - started >= deadline_minutes * 60:
            log.warning("deadline reached: %d firms not reached this run", len(order) - i)
            unread += len(order) - i
            break
        state = states.get(firm.id, FirmState())
        try:
            if firm.source == "page" and polite is None:
                polite = PoliteClient()
            roles, state, out = _process(firm, roles, state, client, http, polite, budget, today, account_error)
        except Exception as exc:
            log.exception("firm %s failed", firm.id)
            out = Outcome(listed=True, errors=[exc])
        account_error = account_error or any(_is_account_error(e) for e in out.errors)
        states[firm.id] = state
        if out.listed:
            failed.append(firm.name)
        elif out.read:
            checked += 1
        else:
            unread += 1
        _save(data_dir, roles, states)

    roles = close_expired(roles, today)
    _save(data_dir, roles, states)
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "firms_checked": checked,
        "firms_failed": failed,
        "firms_unread": unread,
        "firms_not_covered": sorted(not_covered or []),
        "budget_exhausted": budget.exhausted,
        "roles": [r.model_dump(mode="json") for r in sorted(roles.values(), key=lambda r: (r.firm_id, r.title, r.id))],
    }
    _write_json(site_dir / "data" / "roles.json", result)
    if account_error:
        raise RunFailed("AI account error (key, permissions or credit balance); nothing new was enriched")
    if firms and len(failed) == len(firms):
        raise RunFailed("every firm failed")


def _load_not_covered(path: Path) -> list[str]:
    """Firms that refuse automated visits, listed for the website only. A missing file means none."""
    if not path.exists():
        return []
    names = yaml.safe_load(path.read_text()) or []
    if not isinstance(names, list):
        raise ValueError(f"{path} must be a plain list of firm names")
    return [str(n) for n in names]


def warn_uncapped(spend_path: Path, month: str) -> str:
    """Warn on every run without a cap: a free-tier key costs nothing, but a paid key would be billed uncapped."""
    estimate = Budget(spend_path, month, None).spent()
    msg = (f"No AI spending cap is set. Fine for a free-tier Gemini key. If this key is billed, set the repository "
           f"variable AI_MONTHLY_CAP_USD (e.g. 5). At paid prices this month would cost about ${estimate:.2f}.")
    log.warning(msg)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning title=No AI spending cap::{msg}", flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                f.write(f"### ⚠️ No AI spending cap\n\n{msg}\n")
    return msg


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        cap = cap_from_env(os.environ.get("AI_MONTHLY_CAP_USD"))
    except ValueError as e:
        log.error("%s", e)
        raise SystemExit(1) from e
    firms = load_firms(Path("firms/firms.yaml"))
    not_covered = _load_not_covered(Path("firms/blocked.yaml"))
    polite = PoliteClient()
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True,
                          event_hooks=polite.event_hooks()) as http:
            try:
                run(firms, Path("data"), Path("site"), date.today(), GeminiClient(os.environ.get("GEMINI_API_KEY", "")),
                    http, polite=polite, not_covered=not_covered, cap_usd=cap)
            except RunFailed as e:
                log.error("run failed: %s", e)
                raise SystemExit(1) from e
    finally:
        if cap is None:
            warn_uncapped(Path("data") / "spend.json", date.today().strftime("%Y-%m"))


if __name__ == "__main__":
    main()
