"""Unified error model for cn-scraper-mcp.

All errors inherit from ScraperError and carry structured fields
(code, message, retryable, hint).  The error_response() helper
converts any exception into the standard MCP tool result dict:

    {"ok": false, "error": {"code": "...", "message": "...", "retryable": bool, "hint": "..."}}

Engine-specific exceptions (TaobaoAuthError, CDPError, etc.) are
mapped to the appropriate subclass in server.py's exception handlers.
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════
# Base
# ═══════════════════════════════════════════════════════════════

class ScraperError(Exception):
    """Base exception for all scraper errors.

    Attributes:
        code:      Short machine-readable error code (e.g. "COOKIE_EXPIRED").
        message:   Human-readable summary (safe for MCP callers).
        retryable: Whether the caller can retry and expect a different result.
        hint:      Actionable hint for the human operator (never raw exception
                   details).
    """

    code: str = "UNKNOWN"
    retryable: bool = False
    hint: str = ""

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        retryable: bool | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        if hint is not None:
            self.hint = hint

    def to_dict(self) -> dict:
        """Return the standard error dict for MCP tool responses."""
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "hint": self.hint,
            },
        }


# ═══════════════════════════════════════════════════════════════
# Specific error classes
# ═══════════════════════════════════════════════════════════════

class CookieExpiredError(ScraperError):
    """Cookie/credential has expired — re-login required."""

    code = "COOKIE_EXPIRED"
    retryable = False
    hint = "Cookie has expired. Re-login on the platform and refresh the cookie file."

    def __init__(self, message: str = "Cookie expired", **kwargs) -> None:
        super().__init__(message, **kwargs)


class CookieMissingError(ScraperError):
    """Required cookie file not found."""

    code = "COOKIE_MISSING"
    retryable = False
    hint = "Cookie file not found. See README for instructions on exporting cookies."

    def __init__(self, message: str = "Cookie file missing", **kwargs) -> None:
        super().__init__(message, **kwargs)


class AuthRequiredError(ScraperError):
    """Authentication is required but not provided."""

    code = "AUTH_REQUIRED"
    retryable = False
    hint = "This platform requires authentication. Provide a valid cookie file."

    def __init__(self, message: str = "Authentication required", **kwargs) -> None:
        super().__init__(message, **kwargs)


class RateLimitError(ScraperError):
    """Platform rate-limited the request."""

    code = "RATE_LIMITED"
    retryable = True
    hint = "Rate limited by the platform. Wait and retry later."

    def __init__(self, message: str = "Rate limited", **kwargs) -> None:
        super().__init__(message, **kwargs)


class ParseError(ScraperError):
    """Failed to parse platform response (HTML/JSON structure changed)."""

    code = "PARSE_ERROR"
    retryable = False
    hint = "Failed to parse the platform response. The page structure may have changed."

    def __init__(self, message: str = "Parse error", **kwargs) -> None:
        super().__init__(message, **kwargs)


class BrowserError(ScraperError):
    """Browser / CDP communication error."""

    code = "BROWSER_ERROR"
    retryable = True
    hint = "Browser error. Check that Chrome is installed and running with --remote-debugging-port."

    def __init__(self, message: str = "Browser error", **kwargs) -> None:
        super().__init__(message, **kwargs)


class ValidationError(ScraperError):
    """Invalid input parameters."""

    code = "INVALID_INPUT"
    retryable = False
    hint = "Check the input parameters and try again."

    def __init__(self, message: str = "Invalid input", **kwargs) -> None:
        super().__init__(message, **kwargs)


class PlatformError(ScraperError):
    """Generic platform-side error (API returned an unexpected response)."""

    code = "PLATFORM_ERROR"
    retryable = True
    hint = "The platform returned an error. This may be temporary."

    def __init__(self, message: str = "Platform error", **kwargs) -> None:
        super().__init__(message, **kwargs)


# ═══════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════

def error_response(exc: Exception) -> dict:
    """Convert any exception into the standard MCP error dict.

    If *exc* is already a ScraperError, its fields are used directly.
    Otherwise a generic PlatformError wrapper is created so raw
    exception messages are never leaked to MCP callers.
    """
    if isinstance(exc, ScraperError):
        return exc.to_dict()

    # Wrap unknown exceptions — never leak raw messages to callers.
    wrapped = PlatformError(
        message="An unexpected error occurred",
        hint="Check the server logs for details.",
    )
    return wrapped.to_dict()


__all__ = [
    "ScraperError",
    "CookieExpiredError",
    "CookieMissingError",
    "AuthRequiredError",
    "RateLimitError",
    "ParseError",
    "BrowserError",
    "ValidationError",
    "PlatformError",
    "error_response",
]
