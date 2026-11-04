"""What every pipeline step needs, passed explicitly so tests can control time, network and AI."""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from tracker.ai import AIService
from tracker.config import Config
from tracker.http import SafeClient


@dataclass
class Ctx:
    conn: sqlite3.Connection
    now: datetime
    config: Config
    http: SafeClient | None = None
    ai: AIService | None = None
    run_id: int | None = None
    stats: Counter = field(default_factory=Counter)
    clock: Callable[[], float] = time.monotonic
    started: float = field(default_factory=time.monotonic)
    deadline_s: float | None = None
    page_cache: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def today(self) -> date:
        return self.now.date()

    def time_left(self) -> bool:
        return self.deadline_s is None or self.clock() - self.started < self.deadline_s
