"""Proprietary careers pages.

Order of preference, cheapest and most reliable first:
1. Conditional GET: a 304, or the same text hash as last time, ends the check (no AI).
2. schema.org JobPosting JSON-LD embedded in the page: parsed deterministically.
3. Links on the page to a known ATS board are returned as `found_sources`, so the board is monitored directly.
4. Otherwise the page text is handed back with needs_ai=True; the pipeline queues an AI extraction job, so a
   Gemini outage or an exhausted budget never blocks the collection itself.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tracker.connectors import FetchResult, RawPosting, SourceRef
from tracker.http import SafeClient, SourceFailure, links, text_hash
from tracker.titles import looks_like_internship


def _as_list(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def _location(loc) -> str:
    parts = []
    for place in _as_list(loc):
        if not isinstance(place, dict):
            continue
        addr = place.get("address") or {}
        if isinstance(addr, str):
            parts.append(addr)
            continue
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x))
    return "; ".join(p for p in parts if p)


def jsonld_postings(html: str, base_url: str) -> list[dict]:
    """All schema.org JobPosting objects in a page's JSON-LD blocks (including inside @graph and lists)."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            types = _as_list(node.get("@type"))
            if "JobPosting" in types:
                found.append(node)
            for key in ("@graph", "itemListElement", "item"):
                if key in node:
                    walk(node[key])

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or tag.get_text() or "null"))
        except ValueError:
            continue
    return found


def posting_from_jsonld(node: dict, base_url: str) -> RawPosting | None:
    title = " ".join(str(node.get("title") or "").split())
    if not title:
        return None
    url = node.get("url") or node.get("sameAs") or base_url
    if isinstance(url, list):
        url = url[0] if url else base_url
    url = urljoin(base_url, str(url))
    ident = node.get("identifier")
    if isinstance(ident, dict):
        ident = ident.get("value")
    org = node.get("hiringOrganization") or {}
    if isinstance(org, str):
        org = {"name": org}
    desc = BeautifulSoup(str(node.get("description") or ""), "html.parser").get_text(" ")
    return RawPosting(
        external_id=str(ident) if ident else url, title=title, url=url,
        location=_location(node.get("jobLocation")) or ("Remote" if node.get("jobLocationType") else ""),
        description=" ".join(desc.split())[:8000],
        published_at=str(node.get("datePosted") or "")[:10] or None,
        structured={
            "valid_through": str(node.get("validThrough") or "")[:10] or None,
            "employment_type": node.get("employmentType"),
            "hiring_organization": org.get("name") if isinstance(org, dict) else None,
            "hiring_organization_url": (org.get("sameAs") or org.get("url")) if isinstance(org, dict) else None,
        },
        extraction="jsonld",
    )


class CareersPage:
    kind = "page"

    def detect(self, url):
        return None  # any page can be a careers page; sources of this kind are registered explicitly

    def validate(self, ref, http):
        try:
            http.get(ref.url)
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.url

    def fetch(self, source, http: SafeClient) -> FetchResult:
        from tracker.connectors import detect

        resp, text = http.get_text(source["url"], etag=source.get("etag"), last_modified=source.get("last_modified"))
        if resp.not_modified:
            return FetchResult([], not_modified=True, etag=source.get("etag"),
                               last_modified=source.get("last_modified"), content_hash=source.get("content_hash"),
                               http_status=304)
        h = text_hash(text)
        meta = dict(etag=resp.headers.get("etag"), last_modified=resp.headers.get("last-modified"), content_hash=h,
                    http_status=resp.status)
        if h == source.get("content_hash"):
            return FetchResult([], not_modified=True, **meta)
        html = resp.text
        found: dict[str, SourceRef] = {}
        for url, _ in links(html, resp.url):
            ref = detect(url)
            if ref and ref.id not in found:
                found[ref.id] = ref
        nodes = jsonld_postings(html, resp.url)
        postings = [p for p in (posting_from_jsonld(n, resp.url) for n in nodes) if p and looks_like_internship(p.title)]
        if nodes:
            return FetchResult(postings, total_listed=len(nodes), found_sources=list(found.values()), **meta)
        if found:
            # The page points to job boards; those are monitored directly, so the AI is not needed here. Not
            # `complete`: postings previously extracted from this page are left for the boards to confirm.
            return FetchResult([], complete=False, found_sources=list(found.values()), **meta)
        return FetchResult([], complete=False, page_text=text, needs_ai=True, found_sources=list(found.values()),
                           **meta)


def fallback_external_id(url: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{url}|{title}|{location}".encode()).hexdigest()[:16]
