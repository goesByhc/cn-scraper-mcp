"""Structured logging for cn-scraper-mcp.

Writes to stderr (NEVER stdout — MCP uses stdout for protocol).
Never logs cookies, tokens, full response bodies, or auth headers.
Log level controlled by CN_SCRAPER_LOG_LEVEL env var (default: WARNING).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from urllib.parse import urlparse

# ── sensitive patterns for message filtering ─────────────────

_SENSITIVE_HEADERS = {
    "cookie", "set-cookie", "authorization", "x-api-key",
    "x-auth-token", "x-csrf-token", "proxy-authorization",
    "www-authenticate", "x-forwarded-for", "x-real-ip",
}

# If a log record contains any of these keys, the associated value is redacted.
_SENSITIVE_KEY_PATTERNS = [
    "cookie", "token", "secret", "password", "auth",
    "api_key", "apikey", "session", "credential",
]

_COOKIE_RE = None  # compiled on first use


def _get_cookie_re():
    """Lazy-compile the cookie regex (avoids import-time overhead)."""
    global _COOKIE_RE
    if _COOKIE_RE is None:
        import re
        # Match key=value or key: value, capturing the full value
        # Value: any non-bracket/quote chars until a boundary
        _COOKIE_RE = re.compile(
            r'(cookie|token|authorization|auth|session|secret|password|api[_-]?key|credential)'
            r'\s*[:=]\s*[^\n;)\]}"\']+',
            re.IGNORECASE,
        )
    return _COOKIE_RE


# ── URL sanitization ─────────────────────────────────────────

def sanitize_url(url: str) -> str:
    """Return scheme://host/path — strip query string and credentials.

    >>> sanitize_url("https://example.com/api/data?token=abc123&page=2")
    'https://example.com/api/data'
    >>> sanitize_url("https://user:pass@example.com/secret")
    'https://example.com/secret'
    """
    try:
        p = urlparse(url)
        # If no scheme, urlparse puts everything in path — return as-is
        if not p.scheme:
            return url
        host = p.hostname or p.netloc  # hostname strips user:pass@
        if p.port and p.port not in (80, 443):
            host = f"{host}:{p.port}"
        path = p.path or "/"
        return f"{p.scheme}://{host}{path}"
    except Exception:
        return url  # fallback — don't crash on anything


def _sanitize_message(msg: object) -> str:
    """Strip sensitive patterns from a log message string.

    Targets cookie-like key=value pairs, auth tokens, and full JSON bodies.
    Returns a safe copy.
    """
    if not isinstance(msg, str):
        return str(msg)

    # Truncate long messages (full response bodies > 1000 chars are suspicious)
    if len(msg) > 1000:
        msg = msg[:1000] + "…[truncated]"

    # Redact cookie/token patterns
    try:
        msg = _get_cookie_re().sub(r'\1=***REDACTED***', msg)
    except Exception:
        pass

    return msg


class SanitizingFormatter(logging.Formatter):
    """Formatter that sanitizes messages and never leaks sensitive data."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        # Sanitize the message
        record.msg = _sanitize_message(record.msg)
        # Sanitize args if they exist
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: "***REDACTED***" if _is_sensitive_key(k) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    _sanitize_message(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return super().format(record)


def _is_sensitive_key(key: str) -> bool:
    """Check if a key name looks sensitive."""
    key_lower = key.lower().replace("-", "_").replace(" ", "_")
    for pattern in _SENSITIVE_KEY_PATTERNS:
        if pattern in key_lower:
            return True
    return False


# ── error recording ──────────────────────────────────────────

_MAX_ERRORS = 10
_last_errors: deque[dict] = deque(maxlen=_MAX_ERRORS)
_lock = threading.Lock()


def record_error(exc: Exception) -> None:
    """Record an exception for diagnostics (max 10, FIFO eviction).

    The error dict shape:
        {"timestamp": "ISO-8601", "type": "ClassName", "message": "…"}
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "traceback": _format_traceback(exc),
    }
    with _lock:
        _last_errors.append(entry)


def get_recent_errors() -> list[dict]:
    """Return a snapshot of recent recorded errors (newest last)."""
    with _lock:
        return list(_last_errors)


def _format_traceback(exc: Exception) -> str:
    """Format traceback to a short string (last 3 frames)."""
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    # Keep only the last few frames to avoid bloat
    if len(tb_lines) > 8:
        tb_lines = tb_lines[:2] + ["  ... (frames omitted) ...\n"] + tb_lines[-4:]
    return "".join(tb_lines).rstrip()


# ── logger factory ───────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """Return a configured logger that writes to stderr.

    Uses SanitizingFormatter. Never leaks cookies, tokens, or credentials.
    Log level is controlled by CN_SCRAPER_LOG_LEVEL (default: WARNING).

    Usage:
        logger = get_logger(__name__)
        logger.info("Request %s returned %d", url, status)
    """
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(SanitizingFormatter())
        logger.addHandler(handler)

    # Respect env-var log level
    level_name = os.environ.get("CN_SCRAPER_LOG_LEVEL", "WARNING").upper()
    try:
        level = getattr(logging, level_name)
    except AttributeError:
        level = logging.WARNING
    logger.setLevel(level)

    # Don't propagate to root — we handle output ourselves
    logger.propagate = False

    return logger


def set_log_level(level: str) -> None:
    """Set the log level for all cn_scraper_mcp loggers.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL (case-insensitive).
    """
    level_upper = level.upper()
    try:
        numeric = getattr(logging, level_upper)
    except AttributeError:
        numeric = logging.WARNING

    # Update all existing loggers under our namespace
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("cn_scraper_mcp"):
            lg = logging.getLogger(name)
            lg.setLevel(numeric)


__all__ = [
    "get_logger",
    "set_log_level",
    "sanitize_url",
    "record_error",
    "get_recent_errors",
]
