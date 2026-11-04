import httpx
import pytest
import respx

import tracker.fetch as fetch
from tracker.feeds import SourceError
from tracker.fetch import PoliteClient, page_text, text_hash

LONG = "<p>" + "Finance internship in Zurich. " * 20 + "</p>"


def make_client(**kw):
    kw.setdefault("sleep", lambda s: None)
    return PoliteClient(**kw)


def test_page_text_strips_script():
    assert page_text("<p>Intern</p><script>x()</script>") == "Intern"


def test_page_text_strips_style_and_collapses_whitespace():
    assert page_text("<style>p{}</style><p>a \n\n b</p>\n<p>c</p>") == "a b c"


def test_hash_ignores_whitespace_changes():
    assert text_hash(page_text("<p>a  b</p>")) == text_hash(page_text("<p>a b</p>"))


def test_hash_is_sha256_hex():
    assert len(text_hash("x")) == 64


@respx.mock
def test_get_text_returns_visible_text_and_sends_user_agent():
    respx.get("https://a.example/robots.txt").respond(404)
    page = respx.get("https://a.example/jobs").respond(200, text=LONG)
    out = make_client().get_text("https://a.example/jobs")
    assert out.startswith("Finance internship in Zurich.")
    assert page.calls[0].request.headers["user-agent"] == "europe-internship-tracker/1.0"


@respx.mock
def test_robots_disallow_blocks_fetch(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private\n")
    page = respx.get("https://a.example/private/jobs").respond(200, text=LONG)
    browser = []
    monkeypatch.setattr(fetch, "render_with_browser", lambda u: browser.append(u) or "x")
    c = make_client()
    assert c.allowed("https://a.example/private/jobs") is False
    with pytest.raises(SourceError):
        c.get_text("https://a.example/private/jobs")
    assert not page.called
    assert browser == []


@respx.mock
def test_disallowed_short_page_never_reaches_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u: pytest.fail("browser must not be used"))
    with pytest.raises(SourceError):
        make_client().get_text("https://a.example/x")


@respx.mock
def test_unreachable_robots_counts_as_allowed():
    respx.get("https://a.example/robots.txt").mock(side_effect=httpx.ConnectError("down"))
    respx.get("https://a.example/jobs").respond(200, text=LONG)
    c = make_client()
    assert c.allowed("https://a.example/jobs") is True
    assert "internship" in c.get_text("https://a.example/jobs")


@respx.mock
def test_robots_cached_per_host():
    robots = respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/1").respond(200, text=LONG)
    respx.get("https://a.example/2").respond(200, text=LONG)
    c = make_client()
    c.get_text("https://a.example/1")
    c.get_text("https://a.example/2")
    assert robots.call_count == 1


def test_delay_between_same_host():
    now = [100.0]
    stamps = []

    def handler(request):
        stamps.append((request.url.host, now[0]))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=LONG)

    def sleep(s):
        now[0] += s

    c = PoliteClient(clock=lambda: now[0], sleep=sleep,
                     transport=httpx.MockTransport(handler))
    c.get_text("https://a.example/1")
    c.get_text("https://a.example/2")
    c.get_text("https://b.example/1")
    a = [t for h, t in stamps if h == "a.example"]
    assert len(a) == 3  # robots + two pages
    assert all(y - x >= 1.0 for x, y in zip(a, a[1:]))
    assert [t for h, t in stamps if h == "b.example"][0] - a[-1] < 1.0 + 1e-9


def test_browser_fallback_respects_host_delay(monkeypatch):
    now = [100.0]
    events = []

    def handler(request):
        events.append(("http", now[0]))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text="<p>tiny</p>")

    def render(url):
        events.append(("browser", now[0]))
        now[0] += 0.2
        return "rendered " * 40

    monkeypatch.setattr(fetch, "render_with_browser", render)
    c = PoliteClient(clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                     transport=httpx.MockTransport(handler))
    c.get_text("https://a.example/spa")
    http_last = [t for k, t in events if k == "http"][-1]
    browser_t = [t for k, t in events if k == "browser"][0]
    assert browser_t - http_last >= 1.0
    # a following request waits relative to the end of the browser render
    c.get_text("https://a.example/spa2")
    after = [t for k, t in events if k == "http"][-1]
    assert after - (browser_t + 0.2) >= 1.0 - 1e-9


@respx.mock
def test_redirect_cross_host_is_followed_when_allowed():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://b.example/robots.txt").respond(404)
    respx.get("https://a.example/old").respond(302, headers={"location": "https://b.example/new"})
    respx.get("https://b.example/new").respond(200, text=LONG)
    assert "internship" in make_client().get_text("https://a.example/old")


@respx.mock
def test_redirect_to_disallowed_host_raises():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://b.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    respx.get("https://a.example/old").respond(302, headers={"location": "https://b.example/new"})
    target = respx.get("https://b.example/new").respond(200, text=LONG)
    with pytest.raises(SourceError):
        make_client().get_text("https://a.example/old")
    assert not target.called


@respx.mock
def test_redirect_loop_capped_at_five_hops():
    respx.get("https://a.example/robots.txt").respond(404)
    loop = respx.get("https://a.example/loop").respond(302, headers={"location": "/loop"})
    with pytest.raises(SourceError):
        make_client().get_text("https://a.example/loop")
    assert loop.call_count == 6  # initial request + 5 redirects


@respx.mock
def test_robots_redirect_to_disallowing_robots_blocks_page():
    respx.get("http://a.example/robots.txt").respond(
        301, headers={"location": "https://www.a.example/robots.txt"})
    respx.get("https://www.a.example/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private\n")
    page = respx.get("http://a.example/private/x").respond(200, text=LONG)
    c = make_client()
    assert c.allowed("http://a.example/private/x") is False
    with pytest.raises(SourceError):
        c.get_text("http://a.example/private/x")
    assert not page.called


@respx.mock
def test_robots_redirect_loop_counts_as_allowed():
    loop = respx.get("https://a.example/robots.txt").respond(
        302, headers={"location": "/robots.txt"})
    assert make_client().allowed("https://a.example/jobs") is True
    assert loop.call_count == 6  # initial request + 5 hops, then give up


@respx.mock
def test_robots_failures_of_any_kind_count_as_allowed(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    monkeypatch.setattr(fetch.RobotFileParser, "parse",
                        lambda self, lines: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"", 0, 1, "bad")))
    assert make_client().allowed("https://a.example/jobs") is True


def test_invalid_url_is_allowed_by_robots_then_source_error():
    c = make_client()
    assert c.allowed("http://a.example:badport/x") is True
    with pytest.raises(SourceError):
        c.get_text("http://a.example:badport/x")


@respx.mock
def test_short_page_uses_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/spa").respond(200, text="<p>" + "x" * 50 + "</p>")
    seen = []
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u: seen.append(u) or "rendered " * 40)
    out = make_client().get_text("https://a.example/spa")
    assert seen == ["https://a.example/spa"]
    assert out.startswith("rendered")


@respx.mock
def test_long_page_skips_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").respond(200, text=LONG)
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u: pytest.fail("browser should not run"))
    make_client().get_text("https://a.example/jobs")


@respx.mock
def test_http_error_raises_source_error():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").respond(500)
    with pytest.raises(SourceError):
        make_client().get_text("https://a.example/jobs")


@respx.mock
def test_network_error_raises_source_error():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SourceError):
        make_client().get_text("https://a.example/jobs")


def test_render_with_browser_failure_raises_source_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(SourceError):
        fetch.render_with_browser("https://a.example/")
