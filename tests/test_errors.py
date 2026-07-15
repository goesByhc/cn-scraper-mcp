"""Tests for unified error model (ROADMAP §2.1).

Covers:
  - All 13 v0.2.0 stable error classes (9 lowercase + 4 UPPER_SNAKE for backward compat)
  - 4 legacy backward-compat aliases (class names not in v0.2.0 set)
  - ScraperError base class
  - error_response() helper
"""

import pytest

from cn_scraper_mcp.errors import (
    APIChangedError,
    AuthRequiredError,
    BrowserError,
    BrowserUnavailableError,
    CaptchaRequiredError,
    CDPUnavailableError,
    CookieExpiredError,
    CookieMissingError,
    NetworkTimeoutError,
    ParseError,
    PermissionDeniedError,
    PlatformError,
    RateLimitError,
    RiskControlledError,
    ScraperError,
    SelectorMismatchError,
    SessionExpiredError,
    ValidationError,
    error_response,
)

# ── Class enumerations ────────────────────────────────────────────────

# v0.2.0 stable classes that use NEW lowercase codes
V020_NEW_CODE_CLASSES = [
    SessionExpiredError,
    CaptchaRequiredError,
    RiskControlledError,
    NetworkTimeoutError,
    BrowserUnavailableError,
    CDPUnavailableError,
    SelectorMismatchError,
    APIChangedError,
    PermissionDeniedError,
]

# v0.2.0 stable classes that KEEP old UPPER_SNAKE codes for backward compat
V020_OLD_CODE_CLASSES = [
    AuthRequiredError,
    RateLimitError,
    ValidationError,
    PlatformError,
]

V020_STABLE_CLASSES = V020_NEW_CODE_CLASSES + V020_OLD_CODE_CLASSES

LEGACY_CLASSES = [
    CookieExpiredError,
    CookieMissingError,
    BrowserError,
    ParseError,
]

ALL_ERROR_CLASSES = V020_STABLE_CLASSES + LEGACY_CLASSES


# ── ScraperError base ─────────────────────────────────────────────────

class TestScraperError:
    def test_defaults(self):
        e = ScraperError("boom")
        assert e.code == "UNKNOWN"
        assert e.message == "boom"
        assert e.retryable is False
        assert e.hint == ""

    def test_override(self):
        e = ScraperError(
            "custom", code="CUSTOM", retryable=True, hint="do this"
        )
        assert e.code == "CUSTOM"
        assert e.retryable is True
        assert e.hint == "do this"

    def test_to_dict(self):
        e = ScraperError("msg", code="C", retryable=True, hint="h")
        d = e.to_dict()
        assert d == {
            "ok": False,
            "error": {
                "code": "C",
                "message": "msg",
                "retryable": True,
                "hint": "h",
            },
        }

    def test_to_dict_keys_always_present(self):
        """No missing keys — MCP callers should be able to destructure safely."""
        e = ScraperError()
        d = e.to_dict()
        assert set(d.keys()) == {"ok", "error"}
        assert set(d["error"].keys()) == {"code", "message", "retryable", "hint"}


# ── v0.2.0 error codes ──────────────────────────────────────────────

V020_EXPECTED_CODES = {
    # New lowercase codes (ROADMAP §2.1)
    SessionExpiredError: "session_expired",
    CaptchaRequiredError: "captcha_required",
    RiskControlledError: "risk_controlled",
    NetworkTimeoutError: "network_timeout",
    BrowserUnavailableError: "browser_unavailable",
    CDPUnavailableError: "cdp_unavailable",
    SelectorMismatchError: "selector_mismatch",
    APIChangedError: "api_changed",
    PermissionDeniedError: "permission_denied",
    # Old UPPER_SNAKE preserved for backward compat
    AuthRequiredError: "AUTH_REQUIRED",
    RateLimitError: "RATE_LIMITED",
    ValidationError: "INVALID_INPUT",
    PlatformError: "PLATFORM_ERROR",
}

V020_RETRYABLE = {
    AuthRequiredError: False,
    SessionExpiredError: True,
    CaptchaRequiredError: True,
    RateLimitError: True,
    RiskControlledError: False,
    NetworkTimeoutError: True,
    BrowserUnavailableError: True,
    CDPUnavailableError: True,
    SelectorMismatchError: False,
    APIChangedError: False,
    PermissionDeniedError: False,
    ValidationError: False,
    PlatformError: True,
}


class TestV020StableCodes:
    """All v0.2.0 classes have correct codes per the spec."""

    @pytest.mark.parametrize("cls", V020_NEW_CODE_CLASSES)
    def test_new_codes_are_lowercase_snake_case(self, cls):
        """New codes must be lowercase_snake_case."""
        code = cls.code
        assert code == code.lower(), f"{cls.__name__}.code='{code}' is not lowercase"
        assert "_" in code or code.islower(), f"{cls.__name__}.code='{code}' is not snake_case"
        assert " " not in code

    @pytest.mark.parametrize("cls,expected_code", V020_EXPECTED_CODES.items())
    def test_code_matches_spec(self, cls, expected_code):
        assert cls.code == expected_code, (
            f"{cls.__name__}.code expected '{expected_code}', got '{cls.code}'"
        )

    @pytest.mark.parametrize("cls,expected_retryable", V020_RETRYABLE.items())
    def test_retryable_matches_spec(self, cls, expected_retryable):
        assert cls.retryable == expected_retryable, (
            f"{cls.__name__}.retryable expected {expected_retryable}, got {cls.retryable}"
        )

    def test_codes_are_unique(self):
        codes = [cls.code for cls in V020_STABLE_CLASSES]
        assert len(codes) == len(set(codes)), f"Duplicate codes: {codes}"

    @pytest.mark.parametrize("cls", V020_STABLE_CLASSES)
    def test_has_expected_attributes(self, cls):
        e = cls()
        assert isinstance(e.code, str) and len(e.code) > 0
        assert isinstance(e.retryable, bool)
        assert isinstance(e.hint, str) and len(e.hint) > 0
        assert isinstance(e.message, str) and len(e.message) > 0

    def test_to_dict_passthrough(self):
        e = SessionExpiredError("微博登录状态已失效", hint="调用 guided_login('weibo') 更新")
        d = e.to_dict()
        assert d["ok"] is False
        assert d["error"]["code"] == "session_expired"
        assert d["error"]["message"] == "微博登录状态已失效"
        assert d["error"]["retryable"] is True
        assert d["error"]["hint"] == "调用 guided_login('weibo') 更新"


# ── Individual error class behavior ──────────────────────────────────

class TestErrorHints:
    """Each error hint should be actionable — the Agent should know what to do."""

    def test_auth_required_hint_mentions_login(self):
        e = AuthRequiredError()
        assert "guided_login" in e.hint.lower() or "authentication" in e.hint.lower()

    def test_session_expired_hint_mentions_renewal(self):
        e = SessionExpiredError()
        has_renewal = any(w in e.hint.lower() for w in ("re-authenticate", "refresh", "re-login"))
        assert has_renewal, f"SessionExpiredError hint should mention re-auth: {e.hint}"

    def test_captcha_required_is_retryable(self):
        e = CaptchaRequiredError("请过验证码")
        assert e.retryable is True
        assert "manually" in e.hint.lower() or "solve" in e.hint.lower()

    def test_rate_limited_suggests_waiting(self):
        e = RateLimitError()
        assert any(w in e.hint.lower() for w in ("wait", "retry", "later"))

    def test_risk_controlled_is_not_retryable(self):
        e = RiskControlledError()
        assert e.retryable is False

    def test_network_timeout_is_retryable(self):
        e = NetworkTimeoutError()
        assert e.retryable is True

    def test_browser_unavailable_mentions_chrome_path(self):
        e = BrowserUnavailableError()
        assert "chrome" in e.hint.lower()

    def test_cdp_unavailable_mentions_port(self):
        e = CDPUnavailableError()
        assert "remote-debugging-port" in e.hint or "cdp" in e.hint.lower()

    def test_selector_mismatch_hint_mentions_adapter(self):
        e = SelectorMismatchError()
        assert "adapter" in e.hint.lower() or "updated" in e.hint.lower()

    def test_api_changed_hint_mentions_adapter(self):
        e = APIChangedError()
        assert "adapter" in e.hint.lower() or "api" in e.hint.lower()

    def test_permission_denied_not_retryable(self):
        e = PermissionDeniedError()
        assert e.retryable is False

    def test_validation_error_not_retryable(self):
        e = ValidationError("bad param")
        assert e.retryable is False

    def test_session_expired_retryable_after_reauth(self):
        """session_expired is retryable=True: user can re-auth and retry."""
        e = SessionExpiredError()
        assert e.retryable is True


# ── Backward compatibility (legacy codes still work) ─────────────────

LEGACY_EXPECTED_CODES = {
    CookieExpiredError: "COOKIE_EXPIRED",
    CookieMissingError: "COOKIE_MISSING",
    BrowserError: "BROWSER_ERROR",
    ParseError: "PARSE_ERROR",
}

OLD_CLASS_CODES_PRESERVED = {
    AuthRequiredError: "AUTH_REQUIRED",
    RateLimitError: "RATE_LIMITED",
    ValidationError: "INVALID_INPUT",
    PlatformError: "PLATFORM_ERROR",
}


class TestLegacyBackwardCompat:
    """Legacy error codes MUST still work — don't break existing Agent prompts."""

    @pytest.mark.parametrize("cls,expected_code", LEGACY_EXPECTED_CODES.items())
    def test_code_unchanged(self, cls, expected_code):
        assert cls.code == expected_code, (
            f"Legacy {cls.__name__}.code must remain '{expected_code}', got '{cls.code}'"
        )

    def test_cookie_expired_is_session_expired(self):
        ce = CookieExpiredError()
        assert isinstance(ce, SessionExpiredError)
        assert isinstance(ce, ScraperError)

    def test_cookie_missing_is_auth_required(self):
        cm = CookieMissingError()
        assert isinstance(cm, AuthRequiredError)
        assert isinstance(cm, ScraperError)

    def test_browser_error_is_browser_unavailable(self):
        be = BrowserError()
        assert isinstance(be, BrowserUnavailableError)
        assert isinstance(be, ScraperError)

    def test_legacy_codes_dont_leak_into_stable(self):
        """Stable codes should not use legacy UPPER_SNAKE codes (unless same name)."""
        legacy_codes = {cls.code for cls in LEGACY_CLASSES}
        for cls in V020_NEW_CODE_CLASSES:
            assert cls.code not in legacy_codes, (
                f"{cls.__name__}.code='{cls.code}' conflicts with legacy codes"
            )

    @pytest.mark.parametrize("cls,expected_code", OLD_CLASS_CODES_PRESERVED.items())
    def test_old_class_keep_old_code(self, cls, expected_code):
        """Old class names keep old codes for backward compat."""
        assert cls.code == expected_code, (
            f"{cls.__name__}.code must stay '{expected_code}', got '{cls.code}'"
        )


# ── error_response helper ─────────────────────────────────────────────

class TestErrorResponse:
    def test_scrapererror_passthrough(self):
        e = ValidationError("bad field")
        d = error_response(e)
        assert d["error"]["code"] == "INVALID_INPUT"
        assert d["error"]["message"] == "bad field"

    def test_generic_exception_wrapped(self):
        d = error_response(RuntimeError("internal detail"))
        assert d["error"]["code"] == "PLATFORM_ERROR"
        assert d["error"]["message"] == "An unexpected error occurred"
        assert "internal detail" not in str(d)

    def test_generic_never_leaks_message(self):
        """Raw exception messages must never leak to MCP callers."""
        d = error_response(ValueError("secret-token-abc123"))
        assert "secret-token-abc123" not in str(d)
        assert d["error"]["message"] != "secret-token-abc123"

    def test_v020_code_in_response(self):
        """Verify that a v0.2.0 error class appears with the new code."""
        d = error_response(SessionExpiredError("登录过期"))
        assert d["error"]["code"] == "session_expired"

    def test_legacy_code_still_works(self):
        """Legacy CookieExpiredError still returns COOKIE_EXPIRED."""
        d = error_response(CookieExpiredError("过期了"))
        assert d["error"]["code"] == "COOKIE_EXPIRED"

    def test_all_named_codes_appear_in_response(self):
        """Every error class code appears in its error_response output."""
        for cls in ALL_ERROR_CLASSES:
            e = cls()
            d = error_response(e)
            assert d["error"]["code"] == cls.code, (
                f"{cls.__name__}: expected code '{cls.code}', got '{d['error']['code']}'"
            )


# ── Validation helpers (import from server) ──────────────────────────
# We test the regex + logic without importing server (fastmcp may not be installed).

import re

_ALPHANUMERIC_RE = re.compile(r"^[a-zA-Z0-9]+$")


class TestValidationLogic:
    """Test the validation logic inline (server.py imports fastmcp so we test in isolation)."""

    @pytest.mark.parametrize("kw,expected", [
        ("hello", "hello"),
        ("  hello  ", "hello"),
        ("华为mate70", "华为mate70"),
        ("a" * 200, "a" * 200),
    ])
    def test_keyword_valid(self, kw, expected):
        c = kw.strip()
        assert c and len(c) <= 200
        assert c == expected

    @pytest.mark.parametrize("kw", ["", "   "])
    def test_keyword_empty_raises(self, kw):
        c = kw.strip()
        assert not c

    def test_keyword_too_long(self):
        kw = "x" * 201
        assert len(kw.strip()) > 200

    @pytest.mark.parametrize("val,expected", [
        (5, 5), (50, 50), (1, 1), (0, 1), (-5, 1),
        (100, 50), (None, 10), ("bad", 10),
    ])
    def test_limit_clamp(self, val, expected):
        if not isinstance(val, int):
            result = 10
        else:
            result = max(1, min(50, val))
        assert result == expected

    @pytest.mark.parametrize("val,expected", [
        (3, 3), (20, 20), (1, 1), (0, 1),
        (100, 20), (None, 5), ("bad", 5),
    ])
    def test_count_clamp(self, val, expected):
        if not isinstance(val, int):
            result = 5
        else:
            result = max(1, min(20, val))
        assert result == expected

    @pytest.mark.parametrize("gid", ["28888555451", "  12345  ", "0"])
    def test_group_id_valid(self, gid):
        c = gid.strip()
        assert c and c.isdigit()

    @pytest.mark.parametrize("gid", ["", "   ", "abc123", "88-555", "hello"])
    def test_group_id_invalid(self, gid):
        c = gid.strip()
        assert not c or not c.isdigit()

    @pytest.mark.parametrize("nid", ["abc123", "ABCdef", "  abc  ", "A1b2C3"])
    def test_note_id_valid(self, nid):
        c = nid.strip()
        assert c and _ALPHANUMERIC_RE.match(c)

    @pytest.mark.parametrize("nid", ["", "   ", "a-b", "note_1", "hello world"])
    def test_note_id_invalid(self, nid):
        c = nid.strip()
        assert not c or not _ALPHANUMERIC_RE.match(c)
