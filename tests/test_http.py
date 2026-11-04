import httpx
import pytest
import respx

import tracker.http as fetch
from tracker.http import SafeClient, SourceFailure, check_url, page_text, text_hash

LONG = "<p>" + "Finance internship in Zurich. " * 20 + "</p>"


def make_client(**kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("resolver", None)  # test hosts do not resolve; SSRF checks are tested separately below
    return SafeClient(**kw)


def text_of(client, url):
    return client.get_text(url)[1]


def test_page_text_strips_script():
    assert page_text("<p>Intern</p><script>x()</script>") == "Intern"


def test_page_text_keeps_link_targets_inline():
    out = page_text('<p>Apply: <a href="/jobs/42?s=abc">Summer Intern</a> now</p>')
    assert out == "Apply: Summer Intern </jobs/42?s=abc> now"


def test_page_text_skips_non_navigational_links():
    assert page_text('<a href="#top">Top</a><a href="javascript:x()">J</a><a href="mailto:a@b.c">M</a>') == "Top J M"


def test_hash_ignores_link_targets():
    a = page_text('<a href="/job/1?session=aaa">Intern</a>')
    b = page_text('<a href="/job/1?session=bbb">Intern</a>')
    assert a != b
    assert text_hash(a) == text_hash(b)


def test_hash_still_changes_when_text_changes():
    assert text_hash(page_text('<a href="/x">Intern</a>')) != text_hash(page_text('<a href="/x">Analyst</a>'))


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
    out = text_of(make_client(), "https://a.example/jobs")
    assert out.startswith("Finance internship in Zurich.")
    assert page.calls[0].request.headers["user-agent"] == fetch.USER_AGENT


@respx.mock
def test_robots_disallow_blocks_fetch(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(
        200, text="User-agent: *\nDisallow: /private\n")
    page = respx.get("https://a.example/private/jobs").respond(200, text=LONG)
    browser = []
    monkeypatch.setattr(fetch, "render_with_browser", lambda u, r=None: browser.append(u) or "x")
    c = make_client()
    assert c.allowed("https://a.example/private/jobs") is False
    with pytest.raises(SourceFailure):
        text_of(c, "https://a.example/private/jobs")
    assert not page.called
    assert browser == []


@respx.mock
def test_disallowed_short_page_never_reaches_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u, r=None: pytest.fail("browser must not be used"))
    with pytest.raises(SourceFailure):
        text_of(make_client(), "https://a.example/x")


@respx.mock
def test_unreachable_robots_counts_as_allowed():
    respx.get("https://a.example/robots.txt").mock(side_effect=httpx.ConnectError("down"))
    respx.get("https://a.example/jobs").respond(200, text=LONG)
    c = make_client()
    assert c.allowed("https://a.example/jobs") is True
    assert "internship" in text_of(c, "https://a.example/jobs")


@respx.mock
def test_robots_cached_per_host():
    robots = respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/1").respond(200, text=LONG)
    respx.get("https://a.example/2").respond(200, text=LONG)
    c = make_client()
    text_of(c, "https://a.example/1")
    text_of(c, "https://a.example/2")
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

    c = SafeClient(resolver=None, clock=lambda: now[0], sleep=sleep,
                     transport=httpx.MockTransport(handler))
    text_of(c, "https://a.example/1")
    text_of(c, "https://a.example/2")
    text_of(c, "https://b.example/1")
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

    def render(url, resolver=None):
        events.append(("browser", now[0]))
        now[0] += 0.2
        return "rendered " * 40

    monkeypatch.setattr(fetch, "render_with_browser", render)
    c = SafeClient(resolver=None, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                     transport=httpx.MockTransport(handler))
    text_of(c, "https://a.example/spa")
    http_last = [t for k, t in events if k == "http"][-1]
    browser_t = [t for k, t in events if k == "browser"][0]
    assert browser_t - http_last >= 1.0
    # a following request waits relative to the end of the browser render
    text_of(c, "https://a.example/spa2")
    after = [t for k, t in events if k == "http"][-1]
    assert after - (browser_t + 0.2) >= 1.0 - 1e-9


@respx.mock
def test_redirect_cross_host_is_followed_when_allowed():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://b.example/robots.txt").respond(404)
    respx.get("https://a.example/old").respond(302, headers={"location": "https://b.example/new"})
    respx.get("https://b.example/new").respond(200, text=LONG)
    assert "internship" in text_of(make_client(), "https://a.example/old")


@respx.mock
def test_redirect_to_disallowed_host_raises():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://b.example/robots.txt").respond(200, text="User-agent: *\nDisallow: /\n")
    respx.get("https://a.example/old").respond(302, headers={"location": "https://b.example/new"})
    target = respx.get("https://b.example/new").respond(200, text=LONG)
    with pytest.raises(SourceFailure):
        text_of(make_client(), "https://a.example/old")
    assert not target.called


@respx.mock
def test_redirect_loop_capped_at_five_hops():
    respx.get("https://a.example/robots.txt").respond(404)
    loop = respx.get("https://a.example/loop").respond(302, headers={"location": "/loop"})
    with pytest.raises(SourceFailure):
        text_of(make_client(), "https://a.example/loop")
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
    with pytest.raises(SourceFailure):
        text_of(c, "http://a.example/private/x")
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


def test_invalid_url_is_rejected_before_any_request():
    with pytest.raises(SourceFailure) as e:
        text_of(make_client(), "http://a.example:badport/x")
    assert e.value.kind == "unsafe_url"


@respx.mock
def test_short_page_uses_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/spa").respond(200, text="<p>" + "x" * 50 + "</p>")
    seen = []
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u, r=None: seen.append(u) or "rendered " * 40)
    out = text_of(make_client(), "https://a.example/spa")
    assert seen == ["https://a.example/spa"]
    assert out.startswith("rendered")


@respx.mock
def test_long_page_skips_browser(monkeypatch):
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").respond(200, text=LONG)
    monkeypatch.setattr(fetch, "render_with_browser",
                        lambda u, r=None: pytest.fail("browser should not run"))
    text_of(make_client(), "https://a.example/jobs")


@respx.mock
def test_http_error_raises_source_error():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").respond(500)
    with pytest.raises(SourceFailure):
        text_of(make_client(), "https://a.example/jobs")


@respx.mock
def test_network_error_raises_source_error():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(SourceFailure):
        text_of(make_client(), "https://a.example/jobs")


def test_render_with_browser_failure_raises_source_error(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(SourceFailure):
        fetch.render_with_browser("https://a.example/")




# --- new in v2: SSRF guard, failure kinds, size cap, conditional requests --------------------------------------

@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.5/", "http://[::1]/", "http://169.254.169.254/",
                                 "http://localhost/", "http://db.internal/", "ftp://a.example/", "http://u:p@a.example/",
                                 "http://a.example:8080/"])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(SourceFailure) as e:
        check_url(url, resolver=lambda h: ["93.184.216.34"])
    assert e.value.kind == "unsafe_url"


def test_hostname_resolving_to_private_address_is_refused():
    with pytest.raises(SourceFailure) as e:
        check_url("https://evil.example/", resolver=lambda h: ["93.184.216.34", "192.168.1.1"])
    assert e.value.kind == "unsafe_url"


def test_public_hostname_is_allowed():
    assert check_url("https://a.example/jobs", resolver=lambda h: ["93.184.216.34"]) == "https://a.example/jobs"


@respx.mock
def test_redirect_to_private_address_is_refused():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/go").respond(302, headers={"location": "http://127.0.0.1/admin"})
    with pytest.raises(SourceFailure) as e:
        text_of(make_client(), "https://a.example/go")
    assert e.value.kind == "unsafe_url"


@pytest.mark.parametrize("status,kind", [(404, "gone"), (410, "gone"), (401, "auth_required"), (403, "blocked"),
                                         (429, "rate_limited"), (503, "unavailable")])
@respx.mock
def test_http_status_maps_to_failure_kind(status, kind):
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/jobs").respond(status)
    with pytest.raises(SourceFailure) as e:
        make_client().get("https://a.example/jobs")
    assert e.value.kind == kind and e.value.http_status == status


@respx.mock
def test_oversized_response_is_refused():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/big").respond(200, content=b"x" * 2000)
    with pytest.raises(SourceFailure) as e:
        make_client(max_bytes=1000).get("https://a.example/big")
    assert e.value.kind == "invalid"


@respx.mock
def test_conditional_request_returns_not_modified():
    respx.get("https://a.example/robots.txt").respond(404)
    route = respx.get("https://a.example/jobs").respond(304)
    resp = make_client().get("https://a.example/jobs", etag='"v1"', last_modified="Wed, 04 Nov 2026 05:00:00 GMT")
    assert resp.not_modified
    assert route.calls[0].request.headers["if-none-match"] == '"v1"'
    assert route.calls[0].request.headers["if-modified-since"].startswith("Wed")


@respx.mock
def test_invalid_json_is_invalid_failure():
    respx.get("https://a.example/robots.txt").respond(404)
    respx.get("https://a.example/api").respond(200, text="<html>")
    with pytest.raises(SourceFailure) as e:
        make_client().get_json("https://a.example/api")
    assert e.value.kind == "invalid"
