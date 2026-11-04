import math
import json

import pytest

from tracker.budget import Budget
from tracker.budget import INPUT_USD_PER_M, OUTPUT_USD_PER_M


def m(usd):
    """Input tokens that cost `usd` at the current model price, so tests survive price changes."""
    return math.ceil(usd / INPUT_USD_PER_M * 1e6)


def test_spend_recorded_at_model_prices(tmp_path):
    b = Budget(tmp_path / "s.json", "2026-11")
    b.record(1_000_000, 1_000_000)
    assert b.spent() == pytest.approx(INPUT_USD_PER_M + OUTPUT_USD_PER_M, abs=1e-5)


def test_stops_at_4_90(tmp_path):
    b = Budget(tmp_path / "s.json", "2026-11")
    b.record(m(4.80), 0)
    assert b.can_spend() and not b.exhausted
    b.record(m(0.10), 0)
    assert not b.can_spend()
    assert b.exhausted


def test_new_month_resets(tmp_path):
    p = tmp_path / "s.json"
    Budget(p, "2026-10").record(m(4.90), 0)
    b = Budget(p, "2026-11")
    assert b.spent() == 0
    assert b.can_spend()
    b.record(m(0.10), 0)
    data = json.loads(p.read_text())
    assert data["2026-10"] == pytest.approx(4.9, abs=1e-5)
    assert data["2026-11"] == pytest.approx(0.10, abs=1e-5)


def test_record_is_written_immediately_and_reloaded(tmp_path):
    p = tmp_path / "s.json"
    Budget(p, "2026-11").record(m(0.10), 0)
    assert Budget(p, "2026-11").spent() == pytest.approx(0.10, abs=1e-5)


def test_missing_file_starts_at_zero(tmp_path):
    b = Budget(tmp_path / "nope.json", "2026-11")
    assert b.spent() == 0 and b.can_spend()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"2026-11": "lots"}', '{"2026-11": -1}', ""])
def test_corrupt_file_fails_safe_as_exhausted(tmp_path, content):
    p = tmp_path / "s.json"
    p.write_text(content)
    b = Budget(p, "2026-11")
    assert not b.can_spend()
    assert b.exhausted
    b.record(10, 10)  # must not overwrite the damaged file with a reset ledger
    assert p.read_text() == content
    assert not b.can_spend()
