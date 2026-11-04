"""AI as a selective interpretation layer: a small provider interface, one Gemini implementation, and a service that
adds caching, budgets, structured-output validation and an ai_calls record for every call.

`AIService.run` returns a validated pydantic object, or None when the call was not made or should simply be
retried later (budget stop, rate limit, outage). It raises AIUnusable when a billed reply was unusable (refusal,
cut-off, invalid JSON) and AIAccountError when the key or account is the problem; after an account error the
service refuses further calls for the rest of the run. Valid JSON is not trusted as fact: callers validate
content (e.g. evidence quotes must occur in the source text) before anything is stored.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from tracker.budget import Budget, token_cost
from tracker.db import iso, one

log = logging.getLogger(__name__)

MODEL = "gemini-3.5-flash-lite"
API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_TOKENS = 16000
_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "LANGUAGE", "OTHER"}

T = TypeVar("T", bound=BaseModel)


class AIUnusable(Exception):
    """The model replied (and was billed) but the reply could not be used."""


class AIAccountError(Exception):
    """The API rejected the key, permissions or billing: no further calls this run."""


class AITransient(Exception):
    """Rate limit, outage or network trouble: nothing usable, retry later."""


@dataclass
class AIResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    blocked: str | None = None
    search_calls: int = 0
    sources: list[tuple[str, str]] = field(default_factory=list)   # grounding (url, title)
    search_queries: list[str] = field(default_factory=list)


class Provider(Protocol):
    name: str
    model: str

    def generate(self, *, system: str, content: str, schema: dict | None, max_tokens: int,
                 search: bool = False) -> AIResponse: ...


class GeminiProvider:
    """Gemini generateContent over httpx. The key is sent in a header, never in the URL (so it stays out of logs)."""
    name = "gemini"

    def __init__(self, api_key: str, http: httpx.Client | None = None, model: str = MODEL):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.http = http or httpx.Client(timeout=120.0)

    def generate(self, *, system, content, schema, max_tokens, search=False):
        config: dict = {"maxOutputTokens": max_tokens, "thinkingConfig": {"thinkingLevel": "minimal"}}
        if schema is not None:
            config.update(responseMimeType="application/json", responseJsonSchema=schema)
        body: dict = {"systemInstruction": {"parts": [{"text": system}]},
                      "contents": [{"role": "user", "parts": [{"text": content}]}],
                      "generationConfig": config}
        if search:
            body["tools"] = [{"google_search": {}}]
        try:
            r = self.http.post(API_URL.format(model=self.model), json=body, headers={"x-goog-api-key": self.api_key})
        except httpx.TransportError as e:
            raise AITransient(str(e)) from e
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except ValueError:
                msg = r.text
            if r.status_code == 429 or r.status_code >= 500:
                raise AITransient(f"{r.status_code}: {msg}")
            low = msg.lower()
            if r.status_code in (401, 403) or any(w in low for w in ("api key", "billing", "permission", "credit")):
                raise AIAccountError(f"{r.status_code}: {msg}")
            raise AIUnusable(f"{r.status_code}: {msg}")  # input-specific 400/413
        data = r.json()
        usage = data.get("usageMetadata") or {}
        resp = AIResponse(text="", input_tokens=usage.get("promptTokenCount", 0),
                          output_tokens=usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0))
        cands = data.get("candidates") or []
        if not cands:
            resp.blocked = (data.get("promptFeedback") or {}).get("blockReason") or "no candidates"
            return resp
        cand = cands[0]
        resp.finish_reason = cand.get("finishReason")
        if resp.finish_reason in _BLOCKED:
            resp.blocked = resp.finish_reason
        resp.text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])
                            if not p.get("thought"))
        gm = cand.get("groundingMetadata") or {}
        resp.search_queries = list(gm.get("webSearchQueries") or [])
        resp.search_calls = 1 if search and (resp.search_queries or gm.get("groundingChunks")) else 0
        for ch in gm.get("groundingChunks") or []:
            web = ch.get("web") or {}
            if web.get("uri"):
                resp.sources.append((web["uri"], web.get("title") or ""))
        return resp


def input_hash(system: str, content: str) -> str:
    return hashlib.sha256(f"{system}\x00{content}".encode()).hexdigest()


class AIService:
    def __init__(self, conn: sqlite3.Connection, provider: Provider | None, budget: Budget, now: datetime):
        self.conn = conn
        self.provider = provider
        self.budget = budget
        self.now = now
        self.blocked_reason: str | None = None if provider else "no_api_key"
        # Usage rows wait here until flush(), which callers run outside their transactions, so a rolled-back job
        # can never erase the record of a billed call. The budget counts them while they wait.
        self.pending: list[tuple] = []
        budget.pending = self.pending

    @property
    def available(self) -> bool:
        return self.blocked_reason is None and self.provider is not None

    def _record(self, task, version, h, outcome, *, resp: AIResponse | None = None, error: str | None = None,
                search_cost: float = 0.0) -> None:
        cost = (token_cost(resp.input_tokens, resp.output_tokens) if resp else 0.0) + search_cost
        self.pending.append(
            (iso(self.now), task, version, self.provider.model if self.provider else "-", h,
             resp.input_tokens if resp else 0, resp.output_tokens if resp else 0, resp.search_calls if resp else 0,
             cost, outcome, (error or "")[:500] or None))

    def flush(self) -> int:
        """Write buffered usage rows. Call outside any transaction that might roll back."""
        n = len(self.pending)
        self.conn.executemany(
            "INSERT INTO ai_calls (at, task, prompt_version, model, input_hash, input_tokens, output_tokens, "
            "search_calls, cost_usd, outcome, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", self.pending)
        self.pending.clear()
        return n

    def run(self, task: str, version: str, system: str, content: str, model: type[T], *,
            max_tokens: int = MAX_TOKENS, cache: bool = True) -> T | None:
        h = input_hash(system, content)
        if cache:
            row = one(self.conn, "SELECT output FROM ai_cache WHERE task = ? AND prompt_version = ? AND input_hash = ?",
                      (task, version, h))
            if row is not None:
                try:
                    parsed = model.model_validate_json(row["output"])
                    self._record(task, version, h, "cached")
                    return parsed
                except ValidationError:
                    pass
        if not self.available:
            return None
        if not self.budget.can_call():
            self._record(task, version, h, "budget", error=self.budget.exhausted_reason)
            return None
        try:
            resp = self.provider.generate(system=system, content=content, schema=model.model_json_schema(),
                                          max_tokens=max_tokens)
        except AITransient as e:
            self._record(task, version, h, "transient", error=str(e))
            return None
        except AIAccountError as e:
            self._record(task, version, h, "error", error=str(e))
            self.blocked_reason = "account_error"
            log.error("AI account error; no more AI calls this run: %s", e)
            raise
        except AIUnusable as e:
            self._record(task, version, h, "invalid", error=str(e))
            raise
        if resp.blocked:
            self._record(task, version, h, "refused", resp=resp, error=resp.blocked)
            raise AIUnusable(f"model refused ({resp.blocked})")
        if resp.finish_reason == "MAX_TOKENS":
            self._record(task, version, h, "invalid", resp=resp, error="cut off at max tokens")
            raise AIUnusable("reply cut off")
        try:
            parsed = model.model_validate_json(resp.text)
        except ValidationError as e:
            self._record(task, version, h, "invalid", resp=resp, error="schema validation failed")
            raise AIUnusable("invalid model output") from e
        self._record(task, version, h, "ok", resp=resp)
        if cache:
            self.conn.execute("INSERT OR REPLACE INTO ai_cache VALUES (?, ?, ?, ?, ?)",
                              (task, version, h, parsed.model_dump_json(), iso(self.now)))
        return parsed

    def search(self, task: str, version: str, system: str, content: str) -> AIResponse | None:
        """A grounded (web search) call. Returns the raw response, whose `sources` are the URLs found."""
        h = input_hash(system, content)
        if not self.available:
            return None
        if not self.budget.can_search():
            self._record(task, version, h, "budget", error=self.budget.exhausted_reason)
            return None
        try:
            resp = self.provider.generate(system=system, content=content, schema=None, max_tokens=2048, search=True)
        except AITransient as e:
            self._record(task, version, h, "transient", error=str(e))
            return None
        except AIAccountError as e:
            self._record(task, version, h, "error", error=str(e))
            self.blocked_reason = "account_error"
            raise
        except AIUnusable as e:
            self._record(task, version, h, "invalid", error=str(e))
            return None
        self._record(task, version, h, "ok" if not resp.blocked else "refused", resp=resp,
                     search_cost=self.budget.search_cost(resp.search_calls))
        return None if resp.blocked else resp


def norm_quote(text: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


def quote_in(quote: str | None, source_text: str) -> bool:
    """True when an evidence quote really occurs in the source text (punctuation and case ignored)."""
    if not quote or len(quote.strip()) < 4:
        return False
    return norm_quote(quote) in norm_quote(source_text)

