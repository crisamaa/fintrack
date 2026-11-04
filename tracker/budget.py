"""Monthly AI spend ledger, persisted as JSON keyed by month, e.g. {"2026-11": 1.23}."""
import json
import math
import os
from pathlib import Path

MONTHLY_CAP_USD = 5.00
STOP_AT_USD = 4.90
INPUT_USD_PER_M = 0.10
OUTPUT_USD_PER_M = 0.50
_EPS = 1e-9


class Budget:
    def __init__(self, path: Path, month: str):
        self.path = Path(path)
        self.month = month
        self.exhausted = False
        self._corrupt = False
        self._data: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("ledger is not an object")
            data = {}
            for k, v in raw.items():
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                    raise ValueError("bad amount")
                data[str(k)] = float(v)
            self._data = data
        except (OSError, ValueError):
            # Unknown spend: fail safe by treating the month as fully spent.
            self._corrupt = True

    def spent(self) -> float:
        if self._corrupt:
            return MONTHLY_CAP_USD
        return self._data.get(self.month, 0.0)

    def can_spend(self) -> bool:
        ok = not self._corrupt and self.spent() + _EPS < STOP_AT_USD
        if not ok:
            self.exhausted = True
        return ok

    def record(self, input_tokens: int, output_tokens: int) -> None:
        if self._corrupt:
            return  # never overwrite a damaged ledger with a reset one
        cost = input_tokens * INPUT_USD_PER_M / 1e6 + output_tokens * OUTPUT_USD_PER_M / 1e6
        self._data[self.month] = self.spent() + cost
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        os.replace(tmp, self.path)
