import html
from typing import Any

import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from tracker.models import Firm, Posting
from tracker.titles import looks_like_internship

WORKDAY_PAGE_SIZE = 20


class SourceError(Exception):
    """A job source could not be fetched or its response could not be understood."""


def _text(markup: str | None) -> str:
    if not markup:
        return ""
    return BeautifulSoup(markup, "html.parser").get_text(" ", strip=True)


def _join(parts: list[str | None]) -> str:
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return "; ".join(seen)


def _request(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    try:
        resp = client.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        raise SourceError(f"{method} {url}: {e}") from e
    except ValueError as e:
        raise SourceError(f"{method} {url}: invalid JSON") from e


def _greenhouse(firm: Firm, client: httpx.Client) -> list[Posting]:
    data = _request(client, "GET", f"https://boards-api.greenhouse.io/v1/boards/{firm.slug}/jobs",
                    params={"content": "true"})
    return [
        Posting(
            firm_id=firm.id, title=j["title"], url=j["absolute_url"],
            location=(j.get("location") or {}).get("name") or "",
            # The API returns the HTML entity-escaped, so unescape before parsing.
            description=_text(html.unescape(j.get("content") or "")),
        )
        for j in data["jobs"] if looks_like_internship(j["title"])
    ]


def _lever(firm: Firm, client: httpx.Client) -> list[Posting]:
    data = _request(client, "GET", f"https://api.lever.co/v0/postings/{firm.slug}", params={"mode": "json"})
    return [
        Posting(
            firm_id=firm.id, title=j["text"], url=j["hostedUrl"],
            location=_join(j.get("categories", {}).get("allLocations") or [j.get("categories", {}).get("location")]),
            description=j.get("descriptionPlain") or "",
        )
        for j in data if looks_like_internship(j["text"])
    ]


def _ashby(firm: Firm, client: httpx.Client) -> list[Posting]:
    data = _request(client, "GET", f"https://api.ashbyhq.com/posting-api/job-board/{firm.slug}")
    return [
        Posting(
            firm_id=firm.id, title=j["title"], url=j["jobUrl"],
            location=_join([j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]),
            description=j.get("descriptionPlain") or "",
        )
        for j in data["jobs"]
        if j.get("isListed", True) and looks_like_internship(j["title"])
    ]


def _workday(firm: Firm, client: httpx.Client) -> list[Posting]:
    if not firm.site or not firm.slug or "/" not in firm.slug:
        raise SourceError(f"{firm.id}: workday needs site and slug 'tenant/board'")
    tenant, board = firm.slug.split("/", 1)
    api = f"https://{firm.site}/wday/cxs/{tenant}/{board}"
    matched: list[dict] = []
    offset = 0
    while True:
        page = _request(client, "POST", f"{api}/jobs", json={
            "appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE, "offset": offset, "searchText": ""})
        items = page["jobPostings"]
        matched += [j for j in items if looks_like_internship(j["title"])]
        offset += WORKDAY_PAGE_SIZE
        if not items or offset >= page["total"]:
            break
    postings = []
    for j in matched:
        detail = _request(client, "GET", f"{api}{j['externalPath']}")
        postings.append(Posting(
            firm_id=firm.id, title=j["title"], url=f"https://{firm.site}/{board}{j['externalPath']}",
            location=j.get("locationsText") or "",
            description=_text(detail["jobPostingInfo"].get("jobDescription")),
        ))
    return postings


_READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "workday": _workday}


def fetch_feed(firm: Firm, client: httpx.Client) -> list[Posting]:
    reader = _READERS.get(firm.source)
    if reader is None:
        raise SourceError(f"{firm.id}: {firm.source!r} is not a feed source")
    if not firm.slug:
        raise SourceError(f"{firm.id}: missing slug")
    try:
        return reader(firm, client)
    except (KeyError, TypeError, AttributeError, ValidationError) as e:
        raise SourceError(f"{firm.id}: unexpected response shape ({e!r})") from e
