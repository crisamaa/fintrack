import pytest

from conftest import NOW
from tracker.budget import (INPUT_USD_PER_M, SEARCH_FREE_PER_MONTH, STOP_MARGIN_USD, Budget, cap_from_env,
                            token_cost)
from tracker.config import Config, load_config
from tracker.db import iso


def add_call(conn, cost, at=NOW, outcome="ok", searches=0):
    conn.execute("INSERT INTO ai_calls (at, task, prompt_version, model, input_hash, cost_usd, outcome, search_calls) "
                 "VALUES (?, 't', '1', 'm', 'h', ?, ?, ?)", (iso(at), cost, outcome, searches))


def budget(ctx, cap=5.0, daily=100, searches=10):
    return Budget(ctx.conn, NOW, monthly_cap_usd=cap, daily_requests=daily, daily_searches=searches)


def test_cost_at_list_prices():
    assert token_cost(1_000_000, 0) == pytest.approx(INPUT_USD_PER_M)


def test_monthly_cap_stops_at_margin(ctx):
    b = budget(ctx)
    add_call(ctx.conn, 4.80)
    assert b.can_call()
    add_call(ctx.conn, 5.0 - STOP_MARGIN_USD - 4.80)
    assert not b.can_call() and b.exhausted_reason == "monthly_cap"


def test_previous_month_does_not_count(ctx):
    add_call(ctx.conn, 100.0, at=NOW.replace(month=10))
    assert budget(ctx).can_call()


def test_no_cap_never_stops_on_money(ctx):
    add_call(ctx.conn, 1000.0)
    assert budget(ctx, cap=None).can_call()


def test_daily_requests_limit_counts_unflushed_calls(ctx):
    b = budget(ctx, daily=2)
    add_call(ctx.conn, 0.0)
    add_call(ctx.conn, 0.0, outcome="cached")  # cache hits are free and not requests
    assert b.can_call()
    b.pending.append((iso(NOW), "t", "1", "m", "h", 0, 0, 0, 0.0, "ok", None))
    assert not b.can_call() and b.exhausted_reason == "daily_requests"


def test_search_limit_and_free_allowance(ctx):
    b = budget(ctx, searches=2)
    add_call(ctx.conn, 0.0, searches=2)
    assert not b.can_search()
    assert b.search_cost(1) == 0.0  # well inside the monthly free allowance
    add_call(ctx.conn, 0.0, searches=SEARCH_FREE_PER_MONTH)
    assert b.search_cost(1000) == pytest.approx(14.0)


@pytest.mark.parametrize("value,expected", [(None, None), ("", None), ("  ", None), ("5", 5.0), (" 2.5 ", 2.5)])
def test_cap_from_env(value, expected):
    assert cap_from_env(value) == expected


@pytest.mark.parametrize("value", ["abc", "0", "0.05", "-1", "nan", "inf"])
def test_cap_from_env_rejects_bad_values(value):
    with pytest.raises(ValueError):
        cap_from_env(value)


def test_config_reads_env_and_rejects_bad_values(tmp_path):
    cfg = load_config(tmp_path, {"AI_MONTHLY_CAP_USD": "3", "SEARCH_PROVIDER": "gemini", "GEMINI_API_KEY": "k"})
    assert cfg.ai_monthly_cap_usd == 3.0 and cfg.search_provider == "gemini" and cfg.gemini_api_key == "k"
    assert load_config(tmp_path, {}).search_provider == "none"
    with pytest.raises(ValueError):
        load_config(tmp_path, {"AI_MONTHLY_CAP_USD": "lots"})
    with pytest.raises(ValueError):
        load_config(tmp_path, {"SEARCH_PROVIDER": "yahoo"})
    assert isinstance(Config(), Config)
