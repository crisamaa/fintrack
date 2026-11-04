import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tracker import db
from tracker.ai import AIService
from tracker.budget import Budget
from tracker.config import Config
from tracker.context import Ctx
from tracker.http import SafeClient

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 5, 5, 0, tzinfo=UTC)


def fixture_json(name):
    return json.loads((FIX / name).read_text())


def client(handler=None, transport=None):
    """A SafeClient over a mock transport; robots.txt answers 404 unless the handler says otherwise."""
    if transport is None:
        def wrapped(request):
            resp = handler(request)
            if resp is None and request.url.path == "/robots.txt":
                return httpx.Response(404)
            return resp if resp is not None else httpx.Response(404)
        transport = httpx.MockTransport(wrapped)
    return SafeClient(transport=transport, resolver=None, sleep=lambda s: None)


class FakeProvider:
    """Scripted AI provider: `replies` maps task name (from the system prompt) to a JSON string or an exception."""
    name = "fake"
    model = "fake-model-1"

    def __init__(self, replies=None, default=None):
        self.replies = dict(replies or {})
        self.default = default
        self.calls = []

    def generate(self, *, system, content, schema, max_tokens, search=False):
        from tracker.ai import AIResponse
        self.calls.append((system[:40], content))
        for key, reply in self.replies.items():
            if key in system or key in content:
                break
        else:
            reply = self.default
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(content)
        return AIResponse(text=reply, input_tokens=1000, output_tokens=200)


def make_ctx(conn=None, *, now=NOW, http=None, provider=None, config=None, tmp_path=None):
    conn = conn or db.load(Path("/nonexistent-empty-db"), now)
    cfg = config or Config(root=tmp_path or Path("."))
    budget = Budget(conn, now, monthly_cap_usd=cfg.ai_monthly_cap_usd, daily_requests=cfg.ai_daily_requests,
                    daily_searches=cfg.search_daily_limit)
    ai = AIService(conn, provider, budget, now)
    return Ctx(conn=conn, now=now, config=cfg, http=http, ai=ai)


@pytest.fixture
def ctx():
    return make_ctx()
