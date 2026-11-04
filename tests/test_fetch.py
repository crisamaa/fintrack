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


@respx.mock
def test_delay_between_same_host():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/1").respond(200, text=LONG)
    respx.get("https://a.example/2").respond(200, text=LONG)
    respx.get("https://b.example/robots.txt").respond(404)
    respx.get("https://b.example/1").respond(200, text=LONG)
    now = [100.0]
    stamps = []

    def sleep(s):
        now[0] += s

    c = PoliteClient(clock=lambda: now[0], sleep=sleep)
    orig = c._client.send

    def send(request, **kw):
        stamps.append((request.url.host, request.url.path, now[0]))
        return orig(request, **kw)

    c._client.send = send
    c.get_text("https://a.example/1")
    c.get_text("https://a.example/2")
    c.get_text("https://b.example/1")
    a = [t for h, _, t in stamps if h == "a.example"]
    assert all(y - x >= 1.0 for x, y in zip(a, a[1:]))
    assert len(a) == 3  # robots + two pages
    assert [t for h, _, t in stamps if h == "b.example"][0] - a[-1] < 1.0 + 1e-9


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
