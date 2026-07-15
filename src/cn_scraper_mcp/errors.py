"""Unified error model for cn-scraper-mcp.

All errors inherit from ScraperError and carry structured fields
(code, message, retryable, hint).  The error_response() helper
converts any exception into the standard MCP tool result dict:

    {"ok": false, "error": {"code": "...", "message": "...", "retryable": bool, "hint": "..."}}

Error codes follow the stable lowercase scheme defined in ROADMAP.md §2.1:

    auth_required      session_expired    captcha_required
    rate_limited       risk_controlled    network_timeout
    browser_unavailable cdp_unavailable   selector_mismatch
    api_changed        permission_denied

Legacy UPPER_SNAKE_CASE codes (COOKIE_EXPIRED, AUTH_REQUIRED, etc.)
are preserved via compatibility aliases — existing Agent prompts
continue to work without modification.

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
        code:      Short machine-readable error code (e.g. "session_expired").
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
# v0.2.0 stable error classes (ROADMAP §2.1)
# ═══════════════════════════════════════════════════════════════
#
# NOTE: AuthRequiredError, RateLimitError, ValidationError, and PlatformError
# retain their pre-v0.2.0 UPPER_SNAKE_CASE codes for backward compatibility.
# Existing Agent prompts checking for "AUTH_REQUIRED", "RATE_LIMITED", etc.
# must continue to work without modification.
#
# New error classes (SessionExpiredError, CaptchaRequiredError, etc.) use
# the stable lowercase codes defined in ROADMAP §2.1.

class AuthRequiredError(ScraperError):
    """Authentication is required but not provided — user must log in."""

    code = "AUTH_REQUIRED"
    retryable = False
    hint = "This platform requires authentication. Use guided_login() or provide a valid cookie file."

    def __init__(self, message: str = "Authentication required", **kwargs) -> None:
        super().__init__(message, **kwargs)


class SessionExpiredError(ScraperError):
    """Login session or credential has expired — re-login required.

    NOTE: retryable=True because after the user re-authenticates
    (via guided_login or cookie refresh), the operation can succeed.
    """

    code = "session_expired"
    retryable = True
    hint = (
        "Your login session has expired. "
        "Re-authenticate using guided_login() or refresh the cookie file, then retry."
    )

    def __init__(self, message: str = "Session expired", **kwargs) -> None:
        super().__init__(message, **kwargs)


class CaptchaRequiredError(ScraperError):
    """Platform is showing a CAPTCHA — user must solve it manually."""

    code = "captcha_required"
    retryable = True
    hint = (
        "A CAPTCHA challenge is blocking this request. "
        "Open the platform in your browser, solve the CAPTCHA manually, "
        "then retry the operation."
    )

    def __init__(self, message: str = "CAPTCHA required", **kwargs) -> None:
        super().__init__(message, **kwargs)


class RateLimitError(ScraperError):
    """Platform rate-limited the request — wait and retry."""

    code = "RATE_LIMITED"
    retryable = True
    hint = "Rate limited by the platform. Wait a few minutes before retrying."

    def __init__(self, message: str = "Rate limited", **kwargs) -> None:
        super().__init__(message, **kwargs)


class RiskControlledError(ScraperError):
    """Platform flagged the request as suspicious (risk/fraud control)."""

    code = "risk_controlled"
    retryable = False
    hint = (
        "The platform flagged this request as suspicious. "
        "Try reducing request frequency, using a residential IP, "
        "or manually browsing the platform first."
    )

    def __init__(self, message: str = "Risk controlled", **kwargs) -> None:
        super().__init__(message, **kwargs)


class NetworkTimeoutError(ScraperError):
    """Network timeout — the platform did not respond in time."""

    code = "network_timeout"
    retryable = True
    hint = "The request timed out. Check your network connection and retry."

    def __init__(self, message: str = "Network timeout", **kwargs) -> None:
        super().__init__(message, **kwargs)


class BrowserUnavailableError(ScraperError):
    """Browser / Chrome is not available or not running with debugging port."""

    code = "browser_unavailable"
    retryable = True
    hint = (
        "Chrome is not available. "
        "Make sure Chrome is installed and running with --remote-debugging-port, "
        "or set CHROME_PATH to the Chrome executable."
    )

    def __init__(self, message: str = "Browser unavailable", **kwargs) -> None:
        super().__init__(message, **kwargs)


class CDPUnavailableError(ScraperError):
    """Chrome DevTools Protocol connection failed or lost."""

    code = "cdp_unavailable"
    retryable = True
    hint = (
        "CDP connection failed. "
        "Ensure Chrome is running with --remote-debugging-port and the port is correct. "
        "Try restarting the browser if the connection is stale."
    )

    def __init__(self, message: str = "CDP unavailable", **kwargs) -> None:
        super().__init__(message, **kwargs)


class SelectorMismatchError(ScraperError):
    """DOM selector failed — the page structure has changed."""

    code = "selector_mismatch"
    retryable = False
    hint = (
        "The page structure changed and expected DOM elements were not found. "
        "The platform may have updated its layout — the scraper adapter needs to be updated."
    )

    def __init__(self, message: str = "Selector mismatch", **kwargs) -> None:
        super().__init__(message, **kwargs)


class APIChangedError(ScraperError):
    """The platform API returned an unexpected or changed response."""

    code = "api_changed"
    retryable = False
    hint = (
        "The platform API response format has changed. "
        "The scraper adapter may need to be updated to match the new API."
    )

    def __init__(self, message: str = "API changed", **kwargs) -> None:
        super().__init__(message, **kwargs)


class PermissionDeniedError(ScraperError):
    """The user does not have permission to access the requested resource."""

    code = "permission_denied"
    retryable = False
    hint = (
        "You do not have permission to access this content. "
        "Ensure your account has the required access rights (e.g. ZSXQ group membership)."
    )

    def __init__(self, message: str = "Permission denied", **kwargs) -> None:
        super().__init__(message, **kwargs)


class ValidationError(ScraperError):
    """Invalid input parameters."""

    code = "INVALID_INPUT"
    retryable = False
    hint = "Check the input parameters and try again."

    def __init__(self, message: str = "Invalid input", **kwargs) -> None:
        super().__init__(message, **kwargs)


class PlatformError(ScraperError):
    """Generic platform-side error (catch-all for unclassified platform issues)."""

    code = "PLATFORM_ERROR"
    retryable = True
    hint = "The platform returned an error. This may be temporary."

    def __init__(self, message: str = "Platform error", **kwargs) -> None:
        super().__init__(message, **kwargs)


# ═══════════════════════════════════════════════════════════════
# Backward-compatibility aliases (v0.1.x legacy codes)
# ═══════════════════════════════════════════════════════════════
# These classes preserve the old UPPER_SNAKE_CASE error codes for class
# names that do NOT exist in the v0.2.0 stable set.  Existing Agent
# prompts that check for "COOKIE_EXPIRED" or "BROWSER_ERROR" continue
# to work without modification.
#
# For AuthRequiredError, RateLimitError, ValidationError, and
# PlatformError, the class names ARE the same as pre-v0.2.0 and
# their codes have been preserved in place — no separate alias needed.
#
# New code should prefer the v0.2.0 stable classes above.

class CookieExpiredError(SessionExpiredError):
    """[DEPRECATED] Use SessionExpiredError instead.
    Kept for backward compatibility — code is still COOKIE_EXPIRED."""

    code = "COOKIE_EXPIRED"
    hint = "Cookie has expired. Re-login on the platform and refresh the cookie file."

    def __init__(self, message: str = "Cookie expired", **kwargs) -> None:
        super().__init__(message, **kwargs)


class CookieMissingError(AuthRequiredError):
    """[DEPRECATED] Use AuthRequiredError instead.
    Kept for backward compatibility — code is still COOKIE_MISSING."""

    code = "COOKIE_MISSING"
    hint = "Cookie file not found. See README for instructions on exporting cookies."

    def __init__(self, message: str = "Cookie file missing", **kwargs) -> None:
        super().__init__(message, **kwargs)


class BrowserError(BrowserUnavailableError):
    """[DEPRECATED] Use BrowserUnavailableError instead.
    Kept for backward compatibility — code is still BROWSER_ERROR."""

    code = "BROWSER_ERROR"
    hint = "Browser error. Check that Chrome is installed and running with --remote-debugging-port."

    def __init__(self, message: str = "Browser error", **kwargs) -> None:
        super().__init__(message, **kwargs)


class ParseError(ScraperError):
    """[DEPRECATED] Use SelectorMismatchError or APIChangedError instead.
    Kept for backward compatibility — code is still PARSE_ERROR."""

    code = "PARSE_ERROR"
    retryable = False
    hint = "Failed to parse the platform response. The page structure may have changed."

    def __init__(self, message: str = "Parse error", **kwargs) -> None:
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
    # v0.2.0 stable classes
    "ScraperError",
    "AuthRequiredError",
    "SessionExpiredError",
    "CaptchaRequiredError",
    "RateLimitError",
    "RiskControlledError",
    "NetworkTimeoutError",
    "BrowserUnavailableError",
    "CDPUnavailableError",
    "SelectorMismatchError",
    "APIChangedError",
    "PermissionDeniedError",
    "ValidationError",
    "PlatformError",
    # Legacy backward-compatibility aliases (pre-v0.2.0 UPPER_SNAKE codes)
    "CookieExpiredError",
    "CookieMissingError",
    "BrowserError",
    "ParseError",
    # Helper
    "error_response",
]
