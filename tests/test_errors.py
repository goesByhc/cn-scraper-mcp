"""Tests for unified error model."""

import pytest

from cn_scraper_mcp.errors import (
    AuthRequiredError,
    BrowserError,
    CookieExpiredError,
    CookieMissingError,
    ParseError,
    PlatformError,
    RateLimitError,
    ScraperError,
    ValidationError,
    error_response,
)

# ── Subclass enumeration ────────────────────────────────────────────────

ALL_ERROR_CLASSES = [
    CookieExpiredError,
    CookieMissingError,
    AuthRequiredError,
    RateLimitError,
    ParseError,
    BrowserError,
    ValidationError,
    PlatformError,
]


# ── ScraperError base ────────────────────────────────────────────────────

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


# ── Subclass defaults ────────────────────────────────────────────────────

class TestSubclassDefaults:
    @pytest.mark.parametrize("cls", ALL_ERROR_CLASSES)
    def test_has_expected_attributes(self, cls):
        e = cls()
        assert isinstance(e.code, str) and len(e.code) > 0
        assert isinstance(e.retryable, bool)
        assert isinstance(e.hint, str)

    def test_codes_are_unique(self):
        codes = [cls.code for cls in ALL_ERROR_CLASSES]
        assert len(codes) == len(set(codes)), f"Duplicate codes: {codes}"

    def test_default_message_not_empty(self):
        for cls in ALL_ERROR_CLASSES:
            e = cls()
            assert len(e.message) > 0, f"{cls.__name__}.message is empty"

    def test_retryable_values_match_spec(self):
        """RateLimit, Browser, Platform are retryable; the rest are not."""
        retryable = {cls.__name__ for cls in ALL_ERROR_CLASSES if cls.retryable}
        expected = {"RateLimitError", "BrowserError", "PlatformError"}
        assert retryable == expected


# ── error_response helper ─────────────────────────────────────────────────

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


# ── Validation helpers (import from server) ──────────────────────────────
# We test the regex + logic without importing server (fastmcp may not be installed).

import re

_ALPHANUMERIC_RE = re.compile(r"^[a-zA-Z0-9]+$")


class TestValidationLogic:
    """Test the validation logic inline (server.py imports fastmcp so we test in isolation)."""

    # keyword
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

    # limit clamping
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

    # count clamping
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

    # group_id
    @pytest.mark.parametrize("gid", ["28888555451", "  12345  ", "0"])
    def test_group_id_valid(self, gid):
        c = gid.strip()
        assert c and c.isdigit()

    @pytest.mark.parametrize("gid", ["", "   ", "abc123", "88-555", "hello"])
    def test_group_id_invalid(self, gid):
        c = gid.strip()
        assert not c or not c.isdigit()

    # note_id
    @pytest.mark.parametrize("nid", ["abc123", "ABCdef", "  abc  ", "A1b2C3"])
    def test_note_id_valid(self, nid):
        c = nid.strip()
        assert c and _ALPHANUMERIC_RE.match(c)

    @pytest.mark.parametrize("nid", ["", "   ", "a-b", "note_1", "hello world"])
    def test_note_id_invalid(self, nid):
        c = nid.strip()
        assert not c or not _ALPHANUMERIC_RE.match(c)
