"""Polite page fetching: robots.txt, per-host delay, text extraction, browser fallback."""
import hashlib
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from tracker.feeds import SourceError

USER_AGENT = "europe-internship-tracker/1.0"
MIN_TEXT_CHARS = 200


def page_text(html: str) -> str:
    """Visible text with scripts and styles removed and whitespace collapsed."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_with_browser(url: str) -> str:
    """Render a page in headless Chromium and return its visible text."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                page.goto(url, wait_until="networkidle", timeout=30000)
                return page_text(page.content())
            finally:
                browser.close()
    except Exception as e:  # missing playwright/browser, timeouts, navigation errors
        raise SourceError(f"browser render {url}: {e}") from e


class PoliteClient:
    def __init__(
        self,
        min_delay_s: float = 1.0,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 20.0,
    ):
        self.min_delay_s = min_delay_s
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=timeout,
            transport=transport,
        )

    def _get(self, url: str) -> httpx.Response:
        host = urlsplit(url).netloc
        last = self._last.get(host)
        if last is not None:
            wait = self.min_delay_s - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        try:
            return self._client.get(url)
        finally:
            self._last[host] = self._clock()

    def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            parser: RobotFileParser | None = None
            try:
                resp = self._get(f"{parts.scheme}://{host}/robots.txt")
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None  # unreachable robots.txt counts as allowed
            self._robots[host] = parser
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def get_text(self, url: str) -> str:
        if not self.allowed(url):
            raise SourceError(f"{url}: disallowed by robots.txt")
        try:
            resp = self._get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise SourceError(f"GET {url}: {e}") from e
        text = page_text(resp.text)
        if len(text) < MIN_TEXT_CHARS:
            return render_with_browser(url)
        return text
