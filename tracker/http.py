"""Safe, polite HTTP for everything the tracker fetches from the web.

- Only http(s) URLs on default ports, without credentials, whose host resolves to public addresses (SSRF guard;
  checked again on every redirect hop, and for every request a headless browser makes).
- robots.txt is obeyed and a minimum delay is kept between requests to the same host.
- Responses are size-capped and time-limited; redirects are followed by hand so each hop is validated.
- Conditional requests (ETag / Last-Modified) let unchanged sources answer 304 without a body.
- Every failure is raised as SourceFailure with a kind from FAILURE_KINDS, so callers can tell "checked, nothing
  there" apart from "could not check".
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, NavigableString

USER_AGENT = "fintrack-internship-tracker/2.0 (+personal research; respects robots.txt)"
MAX_REDIRECTS = 5
MAX_BYTES = 5_000_000
MIN_TEXT_CHARS = 200

FAILURE_KINDS = ("unavailable", "blocked", "format_changed", "invalid", "pagination", "rate_limited",
                 "auth_required", "gone", "robots", "unsafe_url")


class SourceFailure(Exception):
    """A source could not be checked. `kind` says why; it never means "no vacancies"."""

    def __init__(self, kind: str, message: str, http_status: int | None = None):
        if kind not in FAILURE_KINDS:
            raise ValueError(f"unknown failure kind {kind!r}")
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.http_status = http_status


def failure_for_status(status: int, url: str) -> SourceFailure:
    if status in (404, 410):
        return SourceFailure("gone", f"GET {url}: HTTP {status}", status)
    if status in (401, 407):
        return SourceFailure("auth_required", f"GET {url}: HTTP {status}", status)
    if status == 403:
        return SourceFailure("blocked", f"GET {url}: HTTP 403", status)
    if status == 429:
        return SourceFailure("rate_limited", f"GET {url}: HTTP 429", status)
    return SourceFailure("unavailable", f"GET {url}: HTTP {status}", status)


# --- URL safety --------------------------------------------------------------------------------------------------

Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)})


def _public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    return addr.is_global and not addr.is_multicast


def check_url(url: str, resolver: Resolver | None = system_resolver) -> str:
    """Return the URL if it is safe to fetch, else raise SourceFailure('unsafe_url')."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise SourceFailure("unsafe_url", f"{url!r}: {e}") from e
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceFailure("unsafe_url", f"{url!r}: only http(s) URLs with a host")
    if parts.username or parts.password:
        raise SourceFailure("unsafe_url", f"{url!r}: credentials in URL")
    if port not in (None, 80, 443):
        raise SourceFailure("unsafe_url", f"{url!r}: non-default port")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise SourceFailure("unsafe_url", f"{url!r}: internal host name")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not _public(str(literal)):
            raise SourceFailure("unsafe_url", f"{url!r}: non-public address")
        return url
    if resolver is None:
        return url
    try:
        addrs = resolver(host)
    except OSError as e:
        raise SourceFailure("unavailable", f"{url!r}: DNS lookup failed ({e})") from e
    if not addrs or not all(_public(a) for a in addrs):
        raise SourceFailure("unsafe_url", f"{url!r}: resolves to a non-public address")
    return url


# --- text extraction ---------------------------------------------------------------------------------------------

_LINK_TARGET = re.compile(r"\s*<[^<>\s]+>")
_SKIP_HREF = ("#", "javascript:", "mailto:", "tel:", "data:")


def page_text(html: str) -> str:
    """Visible text, whitespace collapsed, with each link's target kept inline as `text <url>`."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        href = " ".join(str(a["href"]).split())
        if href and not href.lower().startswith(_SKIP_HREF):
            a.append(NavigableString(f" <{href}>"))
    return " ".join(soup.get_text(" ").split())


def text_hash(text: str) -> str:
    """Hash of the text without `<url>` link targets, so rotating tokens in links don't look like changes."""
    stripped = " ".join(_LINK_TARGET.sub("", text).split())
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


def links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(absolute URL, link text) for every http(s) link on a page."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.lower().startswith(_SKIP_HREF):
            continue
        try:
            absolute = urljoin(base_url, href)
        except ValueError:
            continue
        if absolute.startswith(("http://", "https://")):
            out.append((absolute.split("#")[0], " ".join(a.get_text(" ").split())[:200]))
    return out


@dataclass
class Response:
    url: str                    # final URL after redirects
    status: int
    headers: dict[str, str]
    content: bytes = b""
    not_modified: bool = False

    @property
    def text(self) -> str:
        return self.content.decode(_charset(self.headers.get("content-type", "")), errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except ValueError as e:
            raise SourceFailure("invalid", f"{self.url}: response is not JSON") from e


def _charset(content_type: str) -> str:
    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    return m.group(1) if m else "utf-8"


@dataclass
class HttpStats:
    requests: int = 0
    bytes: int = 0
    by_host: dict[str, int] = field(default_factory=dict)


class SafeClient:
    def __init__(self, *, min_delay_s: float = 1.0, transport: httpx.BaseTransport | None = None,
                 resolver: Resolver | None = system_resolver, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 20.0, max_bytes: int = MAX_BYTES,
                 obey_robots: bool = True):
        self.min_delay_s = min_delay_s
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.obey_robots = obey_robots
        self.stats = HttpStats()
        self._clock, self._sleep = clock, sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Language": "en,de;q=0.8,fr;q=0.6"},
                                    follow_redirects=False, timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    # politeness
    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            wait = self.min_delay_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        check_url(url, self.resolver)
        host = urlsplit(url).netloc
        self._wait(host)
        try:
            with self._client.stream(method, url, **kwargs) as resp:
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise SourceFailure("invalid", f"{url}: response larger than {self.max_bytes} bytes")
                resp._content = bytes(body)  # make .content available after the stream closes
                self.stats.requests += 1
                self.stats.bytes += len(body)
                self.stats.by_host[host] = self.stats.by_host.get(host, 0) + 1
                return resp
        except httpx.TimeoutException as e:
            raise SourceFailure("unavailable", f"{method} {url}: timed out") from e
        except httpx.HTTPError as e:
            raise SourceFailure("unavailable", f"{method} {url}: {e}") from e
        finally:
            self._last[host] = self._clock()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            parser: RobotFileParser | None = None
            try:
                robots_url = f"{parts.scheme}://{host}/robots.txt"
                resp = self._send("GET", robots_url)
                hops = 0
                while resp.is_redirect and hops < MAX_REDIRECTS and resp.headers.get("location"):
                    robots_url = urljoin(robots_url, resp.headers["location"])
                    resp = self._send("GET", robots_url)
                    hops += 1
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.content.decode("utf-8", "replace").splitlines())
            except SourceFailure as e:
                if e.kind == "unsafe_url":
                    raise
                parser = None  # robots.txt unreachable counts as allowed (RFC 9309 treats 4xx as allow)
            except Exception:
                parser = None  # an unparseable robots.txt counts as allowed, as in v1
            self._robots[host] = parser
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        if not self.obey_robots:
            return True
        parser = self._robots_for(url)
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def request(self, method: str, url: str, *, etag: str | None = None, last_modified: str | None = None,
                accept: str | None = None, **kwargs) -> Response:
        """Fetch with manual, validated redirects. 304 returns Response(not_modified=True). Errors raise."""
        headers = dict(kwargs.pop("headers", {}) or {})
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        if accept:
            headers["Accept"] = accept
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allowed(url):
                raise SourceFailure("robots", f"{url}: disallowed by robots.txt")
            resp = self._send(method, url, headers=headers, **kwargs)
            if resp.status_code == 304:
                return Response(url, 304, dict(resp.headers), not_modified=True)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise SourceFailure("invalid", f"{method} {url}: redirect without Location")
                url = urljoin(url, location)
                if resp.status_code in (301, 302, 303) and method == "POST":
                    method, kwargs = "GET", {k: v for k, v in kwargs.items() if k not in ("json", "data", "content")}
                continue
            if resp.status_code >= 400:
                raise failure_for_status(resp.status_code, url)
            return Response(url, resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.content)
        raise SourceFailure("invalid", f"{method} {url}: more than {MAX_REDIRECTS} redirects")

    def get(self, url: str, **kwargs) -> Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs) -> Any:
        return self.get(url, accept="application/json", **kwargs).json()

    def post_json(self, url: str, body: Any, **kwargs) -> Any:
        return self.request("POST", url, json=body, accept="application/json", **kwargs).json()

    def get_text(self, url: str, **kwargs) -> tuple[Response, str]:
        """Page text; falls back to a headless browser when the plain HTML has almost no text."""
        resp = self.get(url, **kwargs)
        if resp.not_modified:
            return resp, ""
        text = page_text(resp.text)
        if len(text) < MIN_TEXT_CHARS:
            host = urlsplit(resp.url).netloc
            self._wait(host)
            try:
                text = render_with_browser(resp.url, self.resolver)
            finally:
                self._last[host] = self._clock()
        return resp, text


def render_with_browser(url: str, resolver: Resolver | None = system_resolver) -> str:
    """Render a page in headless Chromium and return its text. Requests to non-public hosts are aborted."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SourceFailure("unavailable", f"browser render {url}: playwright not installed") from e

    def guard(route):
        try:
            check_url(route.request.url, resolver)
        except SourceFailure:
            return route.abort()
        return route.continue_()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.route("**/*", guard)
                page.goto(url, wait_until="networkidle", timeout=30000)
                return page_text(page.content())
            finally:
                browser.close()
    except Exception as e:  # missing browser, timeouts, navigation errors
        raise SourceFailure("unavailable", f"browser render {url}: {e}") from e
