"""AI and search spending limits, computed from the ai_calls table (one row per model call, billed or not).

Three independent limits, all checked before a call starts:
- an optional monthly cap in US dollars (estimated at paid prices; stops STOP_MARGIN_USD below the cap),
- a daily number of model requests (keeps the free tier's daily quota from being hit mid-run),
- a daily number of grounded search requests.
With no monthly cap, spending is still recorded so every run can warn what a paid key would have cost.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from tracker.db import iso, scalar

STOP_MARGIN_USD = 0.10
# Gemini 3.5 Flash-Lite list prices (USD per million tokens), thinking at "minimal". Update with MODEL in ai.py.
INPUT_USD_PER_M = 0.30
OUTPUT_USD_PER_M = 2.50
# Grounding with Google Search on Gemini 3.x: 5,000 free prompts a month, then $14 per 1,000 (checked 2026-11-04).
SEARCH_USD_PER_K = 14.0
SEARCH_FREE_PER_MONTH = 5000
_EPS = 1e-9


def token_cost(input_tokens: int, output_tokens: int) -> float:
    return input_tokens * INPUT_USD_PER_M / 1e6 + output_tokens * OUTPUT_USD_PER_M / 1e6


class Budget:
    def __init__(self, conn: sqlite3.Connection, now: datetime, *, monthly_cap_usd: float | None,
                 daily_requests: int, daily_searches: int):
        self.conn = conn
        self.now = now
        self.cap = monthly_cap_usd
        self.daily_requests = daily_requests
        self.daily_searches = daily_searches
        self.exhausted_reason: str | None = None
        self.pending: list[tuple] = []   # AIService's unflushed usage rows (same column order as ai_calls)

    @property
    def exhausted(self) -> bool:
        return self.exhausted_reason is not None

    def _month(self) -> str:
        return iso(self.now)[:7]

    def _day(self) -> str:
        return iso(self.now)[:10]

    # pending row layout: (at, task, version, model, hash, in_tok, out_tok, search_calls, cost, outcome, error)
    def spent_month(self) -> float:
        stored = scalar(self.conn, "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_calls WHERE substr(at, 1, 7) = ?",
                        (self._month(),)) or 0.0
        return stored + sum(r[8] for r in self.pending if r[0][:7] == self._month())

    def requests_today(self) -> int:
        stored = scalar(self.conn, "SELECT COUNT(*) FROM ai_calls WHERE substr(at, 1, 10) = ? AND outcome NOT IN "
                        "('cached', 'budget')", (self._day(),)) or 0
        return stored + sum(1 for r in self.pending if r[0][:10] == self._day() and r[9] not in ("cached", "budget"))

    def searches_today(self) -> int:
        stored = scalar(self.conn, "SELECT COALESCE(SUM(search_calls), 0) FROM ai_calls WHERE substr(at, 1, 10) = ?",
                        (self._day(),)) or 0
        return stored + sum(r[7] for r in self.pending if r[0][:10] == self._day())

    def searches_month(self) -> int:
        stored = scalar(self.conn, "SELECT COALESCE(SUM(search_calls), 0) FROM ai_calls WHERE substr(at, 1, 7) = ?",
                        (self._month(),)) or 0
        return stored + sum(r[7] for r in self.pending if r[0][:7] == self._month())

    def can_call(self) -> bool:
        if self.cap is not None and self.spent_month() + _EPS >= self.cap - STOP_MARGIN_USD:
            self.exhausted_reason = "monthly_cap"
            return False
        if self.requests_today() >= self.daily_requests:
            self.exhausted_reason = "daily_requests"
            return False
        return True

    def can_search(self) -> bool:
        if self.searches_today() >= self.daily_searches:
            self.exhausted_reason = self.exhausted_reason or "daily_searches"
            return False
        return self.can_call()

    def search_cost(self, calls: int) -> float:
        """Cost of `calls` more grounded searches this month, after the free allowance."""
        used = self.searches_month()
        billable = max(0, used + calls - SEARCH_FREE_PER_MONTH) - max(0, used - SEARCH_FREE_PER_MONTH)
        return billable * SEARCH_USD_PER_K / 1000


def cap_from_env(value: str | None) -> float | None:
    """Parse AI_MONTHLY_CAP_USD. Empty or unset means no cap; anything else must be a number above the margin."""
    if value is None or not value.strip():
        return None
    try:
        cap = float(value.strip())
    except ValueError:
        raise ValueError(f"AI_MONTHLY_CAP_USD must be a number of US dollars, got {value!r}") from None
    if cap != cap or cap in (float("inf"),) or cap <= STOP_MARGIN_USD:
        raise ValueError(f"AI_MONTHLY_CAP_USD must be more than {STOP_MARGIN_USD}, got {value!r}")
    return cap
