"""Connectors for applicant tracking systems with public, documented posting APIs (no key needed).

Greenhouse Job Board API, Lever Postings API, Ashby Posting API, SmartRecruiters Posting API, Recruitee Careers
Site API and Personio's XML feed are documented for public job boards. Workday's /wday/cxs endpoint is the one its
own career sites call; it is not formally documented and may change, so its failures are reported as
format_changed rather than silently returning nothing.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from tracker.connectors import FetchResult, RawPosting, SourceRef
from tracker.http import SourceFailure
from tracker.titles import looks_like_internship

WORKDAY_PAGE_SIZE = 20
SMARTRECRUITERS_PAGE_SIZE = 100
MAX_PAGES = 50


def _text(markup: str | None) -> str:
    if not markup:
        return ""
    return " ".join(BeautifulSoup(markup, "html.parser").get_text(" ").split())


def _join(parts) -> str:
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return "; ".join(seen)


def _ms_to_iso(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, UTC).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _shape(fn):
    """Turn shape surprises in a JSON response into format_changed failures."""
    def wrapper(self, source, http):
        try:
            return fn(self, source, http)
        except (KeyError, TypeError, AttributeError, IndexError) as e:
            raise SourceFailure("format_changed", f"{source['id']}: unexpected response shape ({e!r})") from e
    return wrapper


def _path_slug(url: str, hosts: tuple[str, ...]) -> tuple[str, str] | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in hosts:
        return None
    seg = [s for s in parts.path.split("/") if s]
    if not seg or seg[0] in ("embed", "v1", "api"):
        return None
    return host, seg[0]


class Greenhouse:
    kind = "greenhouse"
    _hosts = ("boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io",
              "job-boards.eu.greenhouse.io")

    def detect(self, url):
        found = _path_slug(url, self._hosts)
        if not found:
            m = re.search(r"[?&]for=([a-z0-9_-]+)", url) if "greenhouse.io/embed" in url else None
            return SourceRef(self.kind, f"https://boards.greenhouse.io/{m.group(1)}", m.group(1)) if m else None
        return SourceRef(self.kind, f"https://boards.greenhouse.io/{found[1]}", found[1].lower())

    def validate(self, ref, http):
        try:
            data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{ref.board}")
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return str(data.get("name") or ref.board) if isinstance(data, dict) else None

    @_shape
    def fetch(self, source, http):
        data = http.get_json(f"https://boards-api.greenhouse.io/v1/boards/{source['board']}/jobs",
                             params={"content": "true"})
        jobs = data["jobs"]
        out = [RawPosting(
            external_id=str(j["id"]), title=j["title"], url=j["absolute_url"],
            location=(j.get("location") or {}).get("name") or "",
            description=_text(html.unescape(j.get("content") or "")),  # the API escapes the HTML
            published_at=(j.get("first_published") or "")[:10] or None,
            structured={"requisition_id": j.get("requisition_id"),
                        "departments": [d.get("name") for d in j.get("departments") or []]},
        ) for j in jobs if looks_like_internship(j["title"])]
        return FetchResult(out, total_listed=len(jobs))


class Lever:
    kind = "lever"

    def detect(self, url):
        found = _path_slug(url, ("jobs.lever.co", "jobs.eu.lever.co"))
        if not found:
            return None
        site = "eu" if found[0] == "jobs.eu.lever.co" else None
        return SourceRef(self.kind, f"https://{found[0]}/{found[1]}", found[1].lower(), site)

    def _api(self, source):
        return "https://api.eu.lever.co" if source.get("site") == "eu" else "https://api.lever.co"

    def validate(self, ref, http):
        try:
            data = http.get_json(f"{self._api({'site': ref.site})}/v0/postings/{ref.board}",
                                 params={"mode": "json", "limit": 1})
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.board if isinstance(data, list) else None

    @_shape
    def fetch(self, source, http):
        data = http.get_json(f"{self._api(source)}/v0/postings/{source['board']}", params={"mode": "json"})
        if not isinstance(data, list):
            raise SourceFailure("format_changed", f"{source['id']}: expected a list")
        out = []
        for j in data:
            if not looks_like_internship(j["text"]):
                continue
            cats = j.get("categories") or {}
            out.append(RawPosting(
                external_id=str(j["id"]), title=j["text"], url=j["hostedUrl"],
                location=_join(cats.get("allLocations") or [cats.get("location")]),
                description=j.get("descriptionPlain") or "",
                published_at=_ms_to_iso(j.get("createdAt")),
                structured={"team": cats.get("team"), "commitment": cats.get("commitment")},
            ))
        return FetchResult(out, total_listed=len(data))


class Ashby:
    kind = "ashby"

    def detect(self, url):
        found = _path_slug(url, ("jobs.ashbyhq.com",))
        return SourceRef(self.kind, f"https://jobs.ashbyhq.com/{found[1]}", found[1]) if found else None

    def validate(self, ref, http):
        try:
            data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{ref.board}")
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.board if isinstance(data, dict) and isinstance(data.get("jobs"), list) else None

    @_shape
    def fetch(self, source, http):
        data = http.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{source['board']}")
        jobs = data["jobs"]
        out = [RawPosting(
            external_id=str(j.get("id") or j["jobUrl"]), title=j["title"], url=j["jobUrl"],
            location=_join([j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]),
            description=j.get("descriptionPlain") or "",
            published_at=(j.get("publishedAt") or "")[:10] or None,
            structured={"employment_type": j.get("employmentType"), "department": j.get("department")},
        ) for j in jobs if j.get("isListed", True) and looks_like_internship(j["title"])]
        return FetchResult(out, total_listed=len(jobs))


class Workday:
    kind = "workday"
    _host = re.compile(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")

    def detect(self, url):
        parts = urlsplit(url)
        m = self._host.match((parts.hostname or "").lower())
        if not m:
            return None
        seg = [s for s in parts.path.split("/") if s]
        if seg and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", seg[0]):
            seg = seg[1:]
        if not seg:
            return None
        return SourceRef(self.kind, f"https://{parts.hostname}/{seg[0]}", f"{m.group(1)}/{seg[0]}", parts.hostname)

    def _api(self, site, board):
        tenant, name = board.split("/", 1)
        return f"https://{site}/wday/cxs/{tenant}/{name}"

    def validate(self, ref, http):
        try:
            page = http.post_json(f"{self._api(ref.site, ref.board)}/jobs",
                                  {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.board if isinstance(page, dict) and "jobPostings" in page else None

    @_shape
    def fetch(self, source, http):
        if not source.get("site") or "/" not in (source.get("board") or ""):
            raise SourceFailure("format_changed", f"{source['id']}: workday needs site and board 'tenant/board'")
        api = self._api(source["site"], source["board"])
        name = source["board"].split("/", 1)[1]
        matched, offset, total = [], 0, None
        for page_no in range(MAX_PAGES):
            try:
                page = http.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE,
                                                      "offset": offset, "searchText": ""})
            except SourceFailure as e:
                if page_no == 0:
                    raise
                raise SourceFailure("pagination", f"{source['id']}: page {page_no + 1} failed ({e})") from e
            items = page["jobPostings"]
            total = page.get("total", total) if page_no == 0 else total
            matched += [j for j in items if looks_like_internship(j["title"])]
            offset += WORKDAY_PAGE_SIZE
            if not items or total is None or offset >= total:
                break
        else:
            raise SourceFailure("pagination", f"{source['id']}: more than {MAX_PAGES} pages")
        out = []
        for j in matched:
            detail = http.get_json(f"{api}{j['externalPath']}")
            info = detail["jobPostingInfo"]
            out.append(RawPosting(
                external_id=j["externalPath"], title=j["title"],
                url=f"https://{source['site']}/{name}{j['externalPath']}",
                location=info.get("location") or j.get("locationsText") or "",
                description=_text(info.get("jobDescription")),
                published_at=(info.get("startDate") or "")[:10] or None,
                structured={"requisition_id": info.get("jobReqId"), "time_type": info.get("timeType")},
            ))
        return FetchResult(out, total_listed=total or len(matched))


class SmartRecruiters:
    kind = "smartrecruiters"

    def detect(self, url):
        found = _path_slug(url, ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"))
        return SourceRef(self.kind, f"https://jobs.smartrecruiters.com/{found[1]}", found[1]) if found else None

    def validate(self, ref, http):
        try:
            data = http.get_json(f"https://api.smartrecruiters.com/v1/companies/{ref.board}/postings",
                                 params={"limit": 1})
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.board if isinstance(data, dict) and "content" in data else None

    @_shape
    def fetch(self, source, http):
        base = f"https://api.smartrecruiters.com/v1/companies/{source['board']}/postings"
        offset, items, total = 0, [], None
        for page_no in range(MAX_PAGES):
            try:
                page = http.get_json(base, params={"limit": SMARTRECRUITERS_PAGE_SIZE, "offset": offset})
            except SourceFailure as e:
                if page_no == 0:
                    raise
                raise SourceFailure("pagination", f"{source['id']}: page {page_no + 1} failed ({e})") from e
            content = page["content"]
            total = page.get("totalFound", total)
            items += content
            offset += SMARTRECRUITERS_PAGE_SIZE
            if not content or total is None or offset >= total:
                break
        out = []
        for j in items:
            if not looks_like_internship(j["name"]):
                continue
            loc = j.get("location") or {}
            detail = http.get_json(f"{base}/{j['id']}")
            sections = (detail.get("jobAd") or {}).get("sections") or {}
            desc = " ".join(_text((sections.get(k) or {}).get("text")) for k in
                            ("jobDescription", "qualifications", "additionalInformation"))
            out.append(RawPosting(
                external_id=str(j["id"]), title=j["name"],
                url=f"https://jobs.smartrecruiters.com/{source['board']}/{j['id']}",
                location=_join([loc.get("city"), (loc.get("country") or "").upper()]),
                description=desc.strip(),
                published_at=(j.get("releasedDate") or "")[:10] or None,
                structured={"ref": j.get("ref"), "department": (j.get("department") or {}).get("label"),
                            "employment": (j.get("typeOfEmployment") or {}).get("label")},
            ))
        return FetchResult(out, total_listed=total or len(items))


class Recruitee:
    kind = "recruitee"
    _host = re.compile(r"^([a-z0-9-]+)\.recruitee\.com$")

    def detect(self, url):
        m = self._host.match((urlsplit(url).hostname or "").lower())
        return SourceRef(self.kind, f"https://{m.group(1)}.recruitee.com", m.group(1)) if m and m.group(1) not in (
            "www", "app", "api") else None

    def validate(self, ref, http):
        try:
            data = http.get_json(f"https://{ref.board}.recruitee.com/api/offers/")
        except SourceFailure as e:
            if e.kind == "gone":
                return None
            raise
        return ref.board if isinstance(data, dict) and isinstance(data.get("offers"), list) else None

    @_shape
    def fetch(self, source, http):
        data = http.get_json(f"https://{source['board']}.recruitee.com/api/offers/")
        offers = data["offers"]
        out = [RawPosting(
            external_id=str(o["id"]), title=o["title"], url=o.get("careers_url") or o["url"],
            location=_join([o.get("city"), o.get("country_code")]) or (o.get("location") or ""),
            description=_text((o.get("description") or "") + " " + (o.get("requirements") or "")),
            published_at=(o.get("published_at") or "")[:10] or None,
            structured={"employment_type": o.get("employment_type_code"), "department": o.get("department")},
        ) for o in offers if looks_like_internship(o["title"])]
        return FetchResult(out, total_listed=len(offers))


class Personio:
    kind = "personio"
    _host = re.compile(r"^([a-z0-9-]+)\.jobs\.personio\.(de|com)$")

    def detect(self, url):
        m = self._host.match((urlsplit(url).hostname or "").lower())
        return SourceRef(self.kind, f"https://{m.group(1)}.jobs.personio.{m.group(2)}", m.group(1),
                         m.group(2)) if m else None

    def _xml(self, source, http):
        tld = source.get("site") or "de"
        resp = http.get(f"https://{source['board']}.jobs.personio.{tld}/xml", params={"language": "en"})
        try:
            return ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise SourceFailure("invalid", f"{source.get('id', source['board'])}: XML does not parse ({e})") from e

    def validate(self, ref, http):
        try:
            root = self._xml({"board": ref.board, "site": ref.site}, http)
        except SourceFailure as e:
            if e.kind in ("gone", "invalid"):
                return None
            raise
        return ref.board if root.tag == "workzag-jobs" else None

    @_shape
    def fetch(self, source, http):
        root = self._xml(source, http)
        if root.tag != "workzag-jobs":
            raise SourceFailure("format_changed", f"{source['id']}: unexpected XML root {root.tag!r}")
        positions = root.findall("position")
        tld = source.get("site") or "de"
        out = []
        for p in positions:
            title = (p.findtext("name") or "").strip()
            if not looks_like_internship(title):
                continue
            pid = (p.findtext("id") or "").strip()
            desc = " ".join(_text(d.findtext("value")) for d in p.iter("jobDescription"))
            out.append(RawPosting(
                external_id=pid, title=title, url=f"https://{source['board']}.jobs.personio.{tld}/job/{pid}",
                location=_join([p.findtext("office")] + [o.text for o in p.iter("additionalOffice")]),
                description=desc, published_at=(p.findtext("createdAt") or "")[:10] or None,
                structured={"employment_type": p.findtext("employmentType"), "schedule": p.findtext("schedule"),
                            "department": p.findtext("department")},
            ))
        return FetchResult(out, total_listed=len(positions))
