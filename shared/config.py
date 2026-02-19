# =============================================================================
# Project Scolecite - Shared Configuration
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Centralized configuration loaded from environment variables.
Supports local development (.env file) and Cloud Run (injected env vars).
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings
from pydantic import Field


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """All environment-driven settings for the trading bot."""

    # ---- AI API Keys ----
    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic (Claude) API key")
    XAI_GROK_API_KEY: str = Field(default="", description="xAI Grok API key")

    # ---- Market Data ----
    POLYGON_API_KEY: str = Field(default="", description="Polygon.io API key")

    # ---- Broker (Alpaca) ----
    APCA_API_KEY_ID: str = Field(default="", description="Alpaca API key ID")
    APCA_API_SECRET_KEY: str = Field(default="", description="Alpaca API secret key")
    APCA_API_BASE_URL: str = Field(
        default="https://paper-api.alpaca.markets",
        description="Alpaca base URL (paper or live)",
    )

    # ---- Trading Mode ----
    TRADING_MODE: str = Field(
        default="paper",
        description="'paper' for simulated trading, 'live' for real orders",
    )

    # ---- Risk Management ----
    MAX_POSITION_PERCENT: float = Field(
        default=5.0,
        description="Maximum position size as % of total portfolio",
    )
    MAX_DRAWDOWN_PERCENT: float = Field(
        default=8.0,
        description="Max drawdown before full liquidation & pause",
    )
    DAILY_LOSS_LIMIT_PERCENT: float = Field(
        default=3.0,
        description="Max intraday loss % before auto-stop",
    )

    # ---- Strategy Loop ----
    STRATEGY_UPDATE_INTERVAL_MIN: int = Field(
        default=180,
        description="Minutes between regular strategy reviews (Opus)",
    )
    GROK_SCAN_INTERVAL_MIN: int = Field(
        default=10,
        description="Minutes between Grok 4.1 Fast data scans",
    )

    # ---- Extended Hours ----
    ALLOW_EXTENDED_HOURS: bool = Field(
        default=False,
        description="Allow pre-market / after-hours trading",
    )

    # ---- Server ----
    SERVER_HOST: str = Field(default="0.0.0.0", description="Server bind host")
    SERVER_PORT: int = Field(default=8000, description="Server bind port")

    # ---- Database ----
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./scolecite.db",
        description="Async DB URL. Use postgresql+asyncpg://... for Cloud Run.",
    )

    # ---- Volatility Thresholds ----
    PRICE_CHANGE_THRESHOLD_PCT: float = Field(
        default=2.5,
        description="Price swing % in 5 min to trigger urgent strategy update",
    )
    VOLUME_SPIKE_MULTIPLIER: float = Field(
        default=3.0,
        description="Volume vs avg multiplier to trigger urgent strategy update",
    )
    SENTIMENT_DROP_THRESHOLD_PCT: float = Field(
        default=40.0,
        description="Sentiment score drop % in 10 min to trigger urgent review",
    )
    INDIVIDUAL_DRAWDOWN_PCT: float = Field(
        default=2.5,
        description="Individual position drawdown % to trigger urgent review",
    )

    # ---- AI Cost Control ----
    DAILY_AI_BUDGET_USD: float = Field(
        default=50.0,
        description="Max daily AI API spend in USD before auto-pause",
    )
    ENABLE_PROMPT_CACHING: bool = Field(
        default=True,
        description="Use Anthropic prompt caching to reduce cost",
    )

    # ---- Market Fatigue ----
    CONSECUTIVE_STOP_LOSS_PAUSE: int = Field(
        default=3,
        description="Consecutive stop-loss hits before 24h trading ban",
    )
    VIX_PANIC_THRESHOLD: float = Field(
        default=30.0,
        description="VIX level above which bot enters defensive mode",
    )

    # ---- Dynamic Universe ----
    DYNAMIC_UNIVERSE_SIZE: int = Field(
        default=10,
        description="Number of tickers Grok selects daily as candidates",
    )
    ENABLE_DYNAMIC_UNIVERSE: bool = Field(
        default=True,
        description="Let Grok auto-select tickers each morning",
    )

    # ---- Confidence Thresholds ----
    HIGH_CONFIDENCE_THRESHOLD: float = Field(
        default=0.90,
        description="Confidence above which position size is increased",
    )
    LOW_CONFIDENCE_THRESHOLD: float = Field(
        default=0.60,
        description="Confidence below which only minimal/no trades",
    )
    HIGH_CONFIDENCE_POSITION_MULT: float = Field(
        default=1.5,
        description="Position size multiplier for high-confidence trades",
    )
    LOW_CONFIDENCE_POSITION_MULT: float = Field(
        default=0.25,
        description="Position size multiplier for low-confidence trades",
    )

    # ---- Timezone ----
    DISPLAY_TIMEZONE: str = Field(
        default="Asia/Seoul",
        description="Timezone shown in client UI (internal always UTC/US-Eastern)",
    )

    # ---- DB Backup ----
    DB_BACKUP_ENABLED: bool = Field(
        default=True,
        description="Enable automatic daily database backup",
    )
    DB_BACKUP_DIR: str = Field(
        default="backups",
        description="Directory for daily DB backups",
    )

    # ---- Social Noise Filter ----
    SOCIAL_NOISE_FILTER_ENABLED: bool = Field(
        default=True,
        description="Enable Grok source-reliability scoring",
    )
    LOW_RELIABILITY_WEIGHT: float = Field(
        default=0.2,
        description="Weight for low-reliability social data in strategy (0-1)",
    )

    @property
    def is_paper(self) -> bool:
        return self.TRADING_MODE.lower() == "paper"

    @property
    def is_local_mode(self) -> bool:
        """True when no API keys are set (development stub mode)."""
        return not self.ANTHROPIC_API_KEY and not self.APCA_API_KEY_ID

    class Config:
        env_file = str(_ENV_FILE) if _ENV_FILE.exists() else None
        env_file_encoding = "utf-8"
        case_sensitive = True
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """Singleton settings loader."""
    return Settings()
