# =============================================================================
# Project Scolecite — Runtime Settings Manager
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Manages user-facing settings stored in ``settings.json``.

Settings that live here (NOT in .env):
  - Trading mode (paper / live)
  - Risk management numbers
  - AI model pairing
  - Ticker universe config
  - Chart defaults
  - Timezone
  - Volatility thresholds
  - Market fatigue
  - Confidence thresholds
  - Social noise filter
  - DB backup toggle
  - Prompt caching toggle

Settings that stay in .env (security / infrastructure):
  - API keys (Anthropic, xAI, Polygon, Alpaca)
  - Alpaca base URL
  - Database URL
  - Server host / port

Design:
  1. ``settings.json`` is the single source of truth for runtime settings.
  2. On first boot the file is created from built-in DEFAULTS.
  3. Every mutation is written atomically and logged to ``settings_history.log``.
  4. ``get_runtime()`` returns the live dict — callers read from it directly.
  5. ``update()`` validates, persists, logs, and hot-reloads in one call.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("scolecite.settings")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SETTINGS_FILE = _PROJECT_ROOT / "settings.json"
_HISTORY_FILE = _PROJECT_ROOT / "settings_history.log"

# ── Built-in defaults (reset target) ────────────────────────────────────────
DEFAULTS: dict[str, Any] = {
    # Trading mode
    "trading_mode": "paper",

    # Risk management
    "max_position_percent": 5.0,
    "max_drawdown_percent": 8.0,
    "daily_loss_limit_percent": 3.0,
    "daily_ai_budget_usd": 50.0,

    # Strategy loop
    "strategy_update_interval_min": 180,
    "grok_scan_interval_min": 10,

    # Extended hours
    "allow_extended_hours": False,

    # Volatility thresholds
    "price_change_threshold_pct": 2.5,
    "volume_spike_multiplier": 3.0,
    "sentiment_drop_threshold_pct": 40.0,
    "individual_drawdown_pct": 2.5,

    # Prompt caching
    "enable_prompt_caching": True,

    # Market fatigue
    "consecutive_stop_loss_pause": 3,
    "vix_panic_threshold": 30.0,

    # Dynamic universe
    "dynamic_universe_size": 10,
    "enable_dynamic_universe": True,
    "fixed_tickers": ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN"],

    # Confidence thresholds
    "high_confidence_threshold": 0.90,
    "low_confidence_threshold": 0.60,
    "high_confidence_position_mult": 1.5,
    "low_confidence_position_mult": 0.25,

    # Timezone
    "display_timezone": "Asia/Seoul",

    # DB backup
    "db_backup_enabled": True,
    "db_backup_dir": "backups",

    # Social noise filter
    "social_noise_filter_enabled": True,
    "low_reliability_weight": 0.2,

    # AI model pairing  (values are API model IDs)
    "ai_model_scan": "grok-3-fast",
    "ai_model_strategy": "grok-3",
    "ai_model_ceo": "claude-opus-4-20250514",

    # Chart defaults
    "default_chart_timeframe": "1h",
    "default_candle_count": 100,

    # AI Adaptive Stop-Loss
    "enable_adaptive_stoploss": False,
    "adaptive_stoploss_hard_cap_pct": 5.0,

    # Universe Filtering
    "universe_min_market_cap_usd": 1_000_000_000,
    "universe_min_volume_usd": 5_000_000,
}

# ── Validation rules ───────────────────────────────────────────────────────
# (key, accepted_types, min, max)  — None means no bound
_RULES: list[tuple[str, type | tuple[type, ...], Any, Any]] = [
    ("trading_mode", str, None, None),
    ("max_position_percent", (int, float), 0.1, 100.0),
    ("max_drawdown_percent", (int, float), 0.1, 100.0),
    ("daily_loss_limit_percent", (int, float), 0.1, 100.0),
    ("daily_ai_budget_usd", (int, float), 0.01, 100000.0),
    ("strategy_update_interval_min", (int, float), 1, 1440),
    ("grok_scan_interval_min", (int, float), 1, 1440),
    ("price_change_threshold_pct", (int, float), 0.01, 100.0),
    ("volume_spike_multiplier", (int, float), 1.0, 100.0),
    ("sentiment_drop_threshold_pct", (int, float), 1.0, 100.0),
    ("individual_drawdown_pct", (int, float), 0.01, 100.0),
    ("consecutive_stop_loss_pause", (int, float), 1, 100),
    ("vix_panic_threshold", (int, float), 1.0, 100.0),
    ("dynamic_universe_size", (int, float), 1, 100),
    ("high_confidence_threshold", (int, float), 0.0, 1.0),
    ("low_confidence_threshold", (int, float), 0.0, 1.0),
    ("high_confidence_position_mult", (int, float), 0.01, 10.0),
    ("low_confidence_position_mult", (int, float), 0.01, 10.0),
    ("low_reliability_weight", (int, float), 0.0, 1.0),
    ("default_candle_count", (int, float), 10, 1000),
    ("adaptive_stoploss_hard_cap_pct", (int, float), 0.5, 50.0),
    ("universe_min_market_cap_usd", (int, float), 0, 1e15),
    ("universe_min_volume_usd", (int, float), 0, 1e12),
]


def _validate(patch: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings (empty = ok)."""
    errors: list[str] = []
    for key, expected_type, lo, hi in _RULES:
        if key not in patch:
            continue
        val = patch[key]
        if not isinstance(val, expected_type):
            name = expected_type.__name__ if isinstance(expected_type, type) else str(expected_type)
            errors.append(f"{key}: expected {name}, got {type(val).__name__}")
            continue
        if lo is not None and val < lo:
            errors.append(f"{key}: must be >= {lo} (got {val})")
        if hi is not None and val > hi:
            errors.append(f"{key}: must be <= {hi} (got {val})")

    if "trading_mode" in patch and patch["trading_mode"] not in ("paper", "live"):
        errors.append("trading_mode: must be 'paper' or 'live'")

    return errors


class SettingsManager:
    """Thread-safe, file-backed runtime settings store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    # ── Public API ──────────────────────────────────────────────────────

    def get_all(self) -> dict[str, Any]:
        """Return a snapshot of all settings (safe to mutate)."""
        with self._lock:
            return copy.deepcopy(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, default)

    def update(self, patch: dict[str, Any]) -> tuple[bool, list[str]]:
        """
        Validate *patch*, merge into current settings, persist, and log.
        Returns (success, error_list).
        """
        errors = _validate(patch)
        if errors:
            return False, errors

        with self._lock:
            old_snapshot = copy.deepcopy(self._data)
            self._data.update(patch)
            self._persist()

        # Log changes
        changes: list[str] = []
        for key, new_val in patch.items():
            old_val = old_snapshot.get(key)
            if old_val != new_val:
                changes.append(f"{key}: {old_val!r} → {new_val!r}")
        if changes:
            self._log_history(changes)

        return True, []

    def reset_defaults(self) -> dict[str, Any]:
        """Overwrite with built-in defaults, persist, return new state."""
        with self._lock:
            self._data = copy.deepcopy(DEFAULTS)
            self._persist()
        self._log_history(["RESET TO DEFAULTS"])
        return self.get_all()

    def get_defaults(self) -> dict[str, Any]:
        return copy.deepcopy(DEFAULTS)

    # ── Internals ───────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load settings.json, creating from defaults if missing."""
        if _SETTINGS_FILE.exists():
            try:
                raw = json.loads(_SETTINGS_FILE.read_text("utf-8"))
                merged = copy.deepcopy(DEFAULTS)
                merged.update(raw)
                self._data = merged
                logger.info("Loaded settings from %s", _SETTINGS_FILE)
                return
            except Exception as exc:
                logger.warning("Corrupt settings.json, resetting: %s", exc)

        self._data = copy.deepcopy(DEFAULTS)
        self._persist()
        logger.info("Created default settings.json")

    def _persist(self) -> None:
        """Atomically write settings.json."""
        tmp = _SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
        tmp.replace(_SETTINGS_FILE)

    def _log_history(self, changes: list[str]) -> None:
        """Append change entries to settings_history.log."""
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [f"[{ts}] {c}" for c in changes]
        try:
            with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            logger.warning("Failed to write settings history: %s", exc)


# ── Module-level singleton ─────────────────────────────────────────────────
_manager: SettingsManager | None = None


def get_settings_manager() -> SettingsManager:
    global _manager
    if _manager is None:
        _manager = SettingsManager()
    return _manager
