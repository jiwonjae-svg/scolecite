# =============================================================================
# Project Scolecite - Logging Utilities
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Structured JSON logging to stdout (Docker/journalctl compatible).
Every AI thought process is captured and forwarded to SSE subscribers.
Includes log sanitization to prevent leaking API keys & account numbers.
"""

from __future__ import annotations

import logging
import re
import sys
import json
from datetime import datetime
from typing import Any, Callable, Optional

import structlog


# ---------------------------------------------------------------------------
# Log Sanitization
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # API keys (generic long alphanumeric strings)
    (re.compile(r"(sk-ant-api\d*-[A-Za-z0-9_-]{20,})", re.I), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"(xai-[A-Za-z0-9_-]{20,})", re.I), "[REDACTED_XAI_KEY]"),
    (re.compile(r"(APCA[A-Za-z0-9_-]{16,})", re.I), "[REDACTED_ALPACA_KEY]"),
    (re.compile(r"(pk_[A-Za-z0-9_-]{20,})", re.I), "[REDACTED_POLYGON_KEY]"),
    # Generic long secrets (32+ hex/alnum)
    (re.compile(r"(?<=['\"])[A-Za-z0-9]{40,}(?=['\"])"), "[REDACTED_SECRET]"),
    # Account numbers (8-12 digit sequences)
    (re.compile(r"\b\d{8,12}\b"), "[REDACTED_ACCOUNT]"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
]


def sanitize_log(text: str) -> str:
    """Redact sensitive data from log strings."""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SanitizingFormatter(logging.Formatter):
    """logging.Formatter that redacts sensitive data."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return sanitize_log(original)


def _setup_stdlib_logging() -> None:
    """Route stdlib logging to stdout with sanitized JSON format."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = SanitizingFormatter(
        '{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}'
    )
    handler.setFormatter(formatter)
    if not root.handlers:
        root.addHandler(handler)


def _sanitize_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog processor that sanitizes all string values."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = sanitize_log(value)
    return event_dict


def setup_logging() -> None:
    """Initialise structured logging (call once at startup)."""
    _setup_stdlib_logging()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _sanitize_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# Thought-process broadcaster (pushes to SSE subscribers)
# ---------------------------------------------------------------------------
_thought_callbacks: list[Callable] = []


def register_thought_callback(callback: Callable) -> None:
    """Register a function to be called on every AI thought log."""
    _thought_callbacks.append(callback)


async def broadcast_thought(
    agent: str,
    action: str,
    thought: str,
    data: dict[str, Any] | None = None,
) -> None:
    """Log + broadcast an AI thought to all registered listeners."""
    logger = get_logger(agent)
    log_entry = {
        "agent": agent,
        "action": action,
        "thought": sanitize_log(thought),
        "data": data or {},
        "timestamp": datetime.utcnow().isoformat(),
    }
    logger.info("thought_process", **log_entry)
    for cb in _thought_callbacks:
        try:
            await cb(log_entry)
        except Exception:
            pass  # never let a subscriber crash the pipeline
