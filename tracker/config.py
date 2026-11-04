"""Central configuration: non-secret settings from config/settings.yaml, secrets and budgets from the environment.

Secrets (GEMINI_API_KEY, BRAVE_API_KEY) are only ever read here, server-side, inside GitHub Actions. They are never
written to the database, the site data or logs.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tracker.taxonomy import COUNTRIES_OF_INTEREST


def _positive(name: str, value: str | None, default: float | None, *, minimum: float = 0.0) -> float | None:
    if value is None or not str(value).strip():
        return default
    try:
        v = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not math.isfinite(v) or v <= minimum:
        raise ValueError(f"{name} must be more than {minimum}, got {value!r}")
    return v


@dataclass
class Config:
    root: Path = Path(".")
    # budgets
    ai_monthly_cap_usd: float | None = None     # None = no cap (fine on the free tier; every run warns)
    ai_daily_requests: int = 400                # hard stop on model calls per UTC day (free-tier friendly)
    search_provider: str = "none"               # none | gemini | brave
    search_daily_limit: int = 20                # 600/month stays inside Gemini 3's 5,000 free grounded prompts
    # run limits
    run_minutes: float = 40.0
    max_source_checks: int = 400
    max_jobs: int = 300
    discovery_candidates_per_run: int = 40
    research_per_run: int = 3
    max_pages_per_candidate: int = 4
    # market scope and notification preferences (non-personal; the private profile lives in the browser)
    countries: frozenset[str] = COUNTRIES_OF_INTEREST
    notify_countries: list[str] = field(default_factory=list)   # empty = all countries of interest
    notify_sectors: list[str] = field(default_factory=list)     # empty = all
    digest_hour_utc: int = 5
    digest_issue: bool = False                  # also open a GitHub issue with the digest (opt-in)
    # secrets
    gemini_api_key: str = ""
    brave_api_key: str = ""

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def db_dir(self) -> Path:
        return self.root / "data" / "db"

    @property
    def site_dir(self) -> Path:
        return self.root / "site"

    @property
    def backup_dir(self) -> Path:
        return self.root / "backups"


def load_config(root: Path = Path("."), env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    root = Path(root)
    settings: dict = {}
    path = root / "config" / "settings.yaml"
    if path.exists():
        settings = yaml.safe_load(path.read_text()) or {}
        if not isinstance(settings, dict):
            raise ValueError(f"{path} must be a mapping")
    budgets = settings.get("budgets") or {}
    limits = settings.get("limits") or {}
    notify = settings.get("notifications") or {}
    cfg = Config(root=root)
    cfg.ai_monthly_cap_usd = _positive("AI_MONTHLY_CAP_USD", env.get("AI_MONTHLY_CAP_USD"),
                                       budgets.get("ai_monthly_cap_usd"), minimum=0.10)
    cfg.ai_daily_requests = int(_positive("AI_DAILY_REQUESTS", env.get("AI_DAILY_REQUESTS"),
                                          budgets.get("ai_daily_requests", cfg.ai_daily_requests)))
    cfg.search_provider = (env.get("SEARCH_PROVIDER") or budgets.get("search_provider") or "none").strip().lower()
    if cfg.search_provider not in ("none", "gemini", "brave"):
        raise ValueError(f"SEARCH_PROVIDER must be none, gemini or brave, got {cfg.search_provider!r}")
    cfg.search_daily_limit = int(_positive("SEARCH_DAILY_LIMIT", env.get("SEARCH_DAILY_LIMIT"),
                                           budgets.get("search_daily_limit", cfg.search_daily_limit)))
    for key in ("run_minutes", "max_source_checks", "max_jobs", "discovery_candidates_per_run", "research_per_run",
                "max_pages_per_candidate"):
        if key in limits:
            setattr(cfg, key, type(getattr(cfg, key))(limits[key]))
    if settings.get("countries"):
        cfg.countries = frozenset(str(c).upper() for c in settings["countries"])
    cfg.notify_countries = [str(c).upper() for c in notify.get("countries") or []]
    cfg.notify_sectors = [str(s) for s in notify.get("sectors") or []]
    cfg.digest_hour_utc = int(notify.get("digest_hour_utc", cfg.digest_hour_utc))
    cfg.digest_issue = str(env.get("DIGEST_ISSUE", notify.get("github_issue", False))).lower() in ("1", "true", "yes")
    cfg.gemini_api_key = env.get("GEMINI_API_KEY", "")
    cfg.brave_api_key = env.get("BRAVE_API_KEY", "")
    return cfg
