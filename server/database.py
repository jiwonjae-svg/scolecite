# =============================================================================
# Project Scolecite - Database Layer
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Async SQLAlchemy database engine, session factory, ORM models, and backup.
Supports SQLite (dev) and PostgreSQL (Cloud Run via Cloud SQL unix socket).
DATABASE_URL examples:
  - SQLite:      sqlite+aiosqlite:///./scolecite.db
  - PostgreSQL:  postgresql+asyncpg://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Text,
    DateTime,
    Boolean,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from shared.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine & Session
# ---------------------------------------------------------------------------
_settings = get_settings()
_is_sqlite = "sqlite" in _settings.DATABASE_URL

# Connection pool tuning (PostgreSQL production defaults)
_pool_kwargs: dict = {}
if not _is_sqlite:
    _pool_kwargs = {
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,      # recycle connections every 30 min
        "pool_pre_ping": True,     # verify connections before use
    }

engine = create_async_engine(
    _settings.DATABASE_URL,
    echo=False,
    future=True,
    connect_args=(
        {"check_same_thread": False} if _is_sqlite else {}
    ),
    **_pool_kwargs,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session."""
    async with async_session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Base Model
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------
class TradeRecord(Base):
    """Persisted trade execution record."""
    __tablename__ = "trade_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, index=True)
    symbol = Column(String(16), nullable=False, index=True)
    side = Column(String(4), nullable=False)  # buy / sell
    qty = Column(Float, nullable=False)
    filled_price = Column(Float, nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    reason = Column(Text, default="")
    strategy_version = Column(Integer, default=1)
    message = Column(Text, default="")
    confidence = Column(Float, default=0.5)
    created_at = Column(DateTime, default=func.now())


class ThoughtRecord(Base):
    """AI thought-process log persisted for review."""
    __tablename__ = "thought_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent = Column(String(32), nullable=False, index=True)
    action = Column(String(128), nullable=False)
    thought = Column(Text, nullable=False)
    data_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=func.now())

    def set_data(self, data: dict) -> None:
        self.data_json = json.dumps(data, default=str)

    def get_data(self) -> dict:
        raw = self.data_json
        return json.loads(str(raw)) if raw is not None else {}


class ReviewRecord(Base):
    """Post-trade self-correction review by Opus."""
    __tablename__ = "review_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), index=True)
    symbol = Column(String(16), nullable=False)
    expected_outcome = Column(Text, default="")
    actual_outcome = Column(Text, default="")
    error_analysis = Column(Text, default="")
    improvement = Column(Text, default="")
    strategy_version = Column(Integer, default=1)
    created_at = Column(DateTime, default=func.now())


class StrategyRecord(Base):
    """Versioned snapshot of the strategy produced by Opus."""
    __tablename__ = "strategy_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False, unique=True)
    reasoning = Column(Text, nullable=False)
    actions_json = Column(Text, default="[]")
    risk_notes = Column(Text, default="")
    confidence = Column(Float, default=0.5)
    self_correction = Column(Text, default="")
    token_usage_json = Column(Text, default="{}")
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=func.now())


class AIUsageRecord(Base):
    """Token / cost tracking per AI call."""
    __tablename__ = "ai_usage_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String(32), nullable=False)  # grok_fast, grok_strategy, opus
    model = Column(String(64), nullable=False)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    purpose = Column(String(128), default="")
    created_at = Column(DateTime, default=func.now())


class ChatRecord(Base):
    """Persisted AI chat messages."""
    __tablename__ = "chat_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    context = Column(String(32), default="strategy")
    cost_usd = Column(Float, default=0.0)
    created_at = Column(DateTime, default=func.now())


class JournalRecord(Base):
    """Auto-generated daily trade journal."""
    __tablename__ = "journal_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True, index=True)  # "2025-01-15"
    summary = Column(Text, nullable=False)
    total_trades = Column(Integer, default=0)
    winning_trades = Column(Integer, default=0)
    losing_trades = Column(Integer, default=0)
    net_pnl = Column(Float, default=0.0)
    key_lessons_json = Column(Text, default="[]")
    strategy_version = Column(Integer, default=0)
    ai_commentary = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())

    def set_lessons(self, lessons: list[str]) -> None:
        self.key_lessons_json = json.dumps(lessons)

    def get_lessons(self) -> list[str]:
        raw = self.key_lessons_json
        return json.loads(str(raw)) if raw is not None else []


class CandidateRecord(Base):
    """Persisted ticker-universe candidates from Grok Fast."""
    __tablename__ = "candidate_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    reason = Column(Text, default="")
    sector = Column(String(64), default="")
    score = Column(Float, default=0.0)
    source = Column(String(64), default="")
    valid_date = Column(String(10), nullable=False, index=True)
    created_at = Column(DateTime, default=func.now())


class BacktestRecord(Base):
    """Persisted backtest results."""
    __tablename__ = "backtest_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)
    start_date = Column(String(10), nullable=False)
    end_date = Column(String(10), nullable=False)
    initial_capital = Column(Float, default=100000.0)
    final_capital = Column(Float, default=0.0)
    total_return_pct = Column(Float, default=0.0)
    max_drawdown_pct = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    total_trades = Column(Integer, default=0)
    result_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=func.now())

    def set_result(self, result: dict) -> None:
        self.result_json = json.dumps(result, default=str)

    def get_result(self) -> dict:
        raw = self.result_json
        return json.loads(str(raw)) if raw is not None else {}


# ---------------------------------------------------------------------------
# Table Creation Helper
# ---------------------------------------------------------------------------
async def init_db() -> None:
    """Create all tables. Safe to call multiple times.
    Retries on Cloud SQL cold-start connection delays.
    """
    import asyncio

    max_retries = 5 if not _is_sqlite else 1
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            db_type = "PostgreSQL (Cloud SQL)" if not _is_sqlite else "SQLite"
            logger.info("Database ready — %s", db_type)
            return
        except Exception as exc:
            if attempt == max_retries:
                logger.error("Failed to init DB after %d attempts: %s", max_retries, exc)
                raise
            wait = 2 ** attempt
            logger.warning("DB init attempt %d/%d failed, retrying in %ds: %s",
                           attempt, max_retries, wait, exc)
            await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Database Backup (SQLite only)
# ---------------------------------------------------------------------------
def backup_sqlite_db() -> str | None:
    """
    Create a timestamped copy of the SQLite database file.
    Returns the backup path, or None if not applicable (e.g. PostgreSQL).
    For Cloud SQL (PostgreSQL), use Cloud SQL automated backups instead.
    """
    if not _is_sqlite:
        return None

    # Extract the file path from the URL
    # "sqlite+aiosqlite:///./scolecite.db" → "./scolecite.db"
    db_path_str = _settings.DATABASE_URL.split("///")[-1]
    db_path = Path(db_path_str).resolve()
    if not db_path.exists():
        return None

    backup_dir = Path(_settings.DB_BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"scolecite_{stamp}.db"
    shutil.copy2(str(db_path), str(backup_path))

    # Keep only the latest 7 backups
    backups = sorted(backup_dir.glob("scolecite_*.db"), reverse=True)
    for old in backups[7:]:
        old.unlink(missing_ok=True)

    return str(backup_path)
