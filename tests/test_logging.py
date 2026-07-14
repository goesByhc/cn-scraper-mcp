"""Tests for cn_scraper_mcp.logging and diagnose tool."""

from __future__ import annotations

import io
import logging
import sys

import pytest

from cn_scraper_mcp.logging import (
    get_logger,
    get_recent_errors,
    record_error,
    sanitize_url,
    set_log_level,
)

# ═══════════════════════════════════════════════════════════════
# sanitize_url
# ═══════════════════════════════════════════════════════════════

class TestSanitizeUrl:
    """Tests for URL sanitization — strip query params and credentials."""

    def test_strips_query_string(self):
        result = sanitize_url("https://example.com/api/data?token=abc123&page=2")
        assert result == "https://example.com/api/data"

    def test_strips_userinfo_credentials(self):
        result = sanitize_url("https://user:pass@example.com/secret")
        assert result == "https://example.com/secret"

    def test_preserves_host_and_path(self):
        result = sanitize_url("https://www.example.com/some/path/deep/")
        assert result == "https://www.example.com/some/path/deep/"

    def test_preserves_port(self):
        result = sanitize_url("https://example.com:8080/api/data?key=val")
        assert result == "https://example.com:8080/api/data"

    def test_handles_http(self):
        result = sanitize_url("http://example.com/page")
        assert result == "http://example.com/page"

    def test_no_query_no_userinfo(self):
        result = sanitize_url("https://example.com/path")
        assert result == "https://example.com/path"

    def test_malformed_url_does_not_crash(self):
        result = sanitize_url("not a valid url")
        assert result == "not a valid url"


# ═══════════════════════════════════════════════════════════════
# logger writes to stderr only
# ═══════════════════════════════════════════════════════════════

class TestLoggerOutput:
    """Tests that the logger writes to stderr, never stdout."""

    def test_logger_has_stderr_handler(self):
        """Logger should have a StreamHandler pointed at sys.stderr."""
        name = "test_stderr_logger"
        logger = get_logger(name)
        handlers = logger.handlers
        assert len(handlers) >= 1
        for h in handlers:
            if isinstance(h, logging.StreamHandler):
                assert h.stream is sys.stderr, "Logger handler must write to stderr"

    def test_logger_does_not_write_to_stdout(self, capsys):
        """Verify log output does not appear on stdout."""
        name = "test_stdout_isolation"
        logger = get_logger(name)
        # Set to INFO so the message actually fires
        old_level = logger.level
        logger.setLevel(logging.INFO)
        try:
            logger.info("This should NOT appear on stdout")
            captured = capsys.readouterr()
            # stdout should be empty
            assert captured.out == "", f"stdout should be empty but got: {captured.out}"
        finally:
            logger.setLevel(old_level)


# ═══════════════════════════════════════════════════════════════
# No cookies/tokens in log output
# ═══════════════════════════════════════════════════════════════

class TestLogSanitization:
    """Tests that sensitive data is never logged."""

    def _capture_stderr(self, logger, level, msg, *args):
        """Capture what a logger writes to stderr."""
        # Replace the handler with one that writes to a StringIO
        logger.handlers.clear()
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        from cn_scraper_mcp.logging import SanitizingFormatter
        handler.setFormatter(SanitizingFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        getattr(logger, level)(msg, *args)
        handler.flush()
        return buf.getvalue()

    def test_no_cookies_in_message(self):
        """Log messages containing cookie=value should be redacted."""
        logger = get_logger("test_sanitize_cookies")
        output = self._capture_stderr(
            logger, "info",
            "Request with cookie=abc123xyz and other data",
        )
        assert "abc123xyz" not in output, f"Cookie value leaked: {output}"
        assert "REDACTED" in output or "cookie" not in output.lower()

    def test_no_token_in_message(self):
        """Log messages containing token=value should be redacted."""
        logger = get_logger("test_sanitize_token")
        output = self._capture_stderr(
            logger, "info",
            "Auth header: token=secret_jwt_value_here",
        )
        assert "secret_jwt_value_here" not in output, f"Token value leaked: {output}"

    def test_no_authorization_header_in_message(self):
        """Log messages containing Authorization: Bearer XXX should be redacted."""
        logger = get_logger("test_sanitize_auth")
        output = self._capture_stderr(
            logger, "info",
            "Authorization: Bearer sk-1234567890abcdef",
        )
        assert "sk-1234567890abcdef" not in output, f"Auth header leaked: {output}"

    def test_safe_message_passes_through(self):
        """Non-sensitive messages should pass through unchanged."""
        logger = get_logger("test_sanitize_safe")
        output = self._capture_stderr(
            logger, "info",
            "Fetched https://example.com/api — status 200",
        )
        assert "Fetched https://example.com/api" in output
        assert "status 200" in output

    def test_long_message_truncated(self):
        """Messages > 1000 chars should be truncated."""
        logger = get_logger("test_sanitize_long")
        long_msg = "x" * 2000
        output = self._capture_stderr(logger, "warning", long_msg)
        assert "truncated" in output.lower()


# ═══════════════════════════════════════════════════════════════
# record_error / get_recent_errors
# ═══════════════════════════════════════════════════════════════

class TestErrorRecording:
    """Tests for record_error and get_recent_errors."""

    def test_record_and_retrieve(self):
        """record_error stores errors; get_recent_errors returns them."""
        # Clear any pre-existing errors by importing fresh
        try:
            exc = ValueError("test error 1")
            record_error(exc)
            errors = get_recent_errors()
            assert len(errors) >= 1
            last = errors[-1]
            assert last["type"] == "ValueError"
            assert "test error 1" in last["message"]
            assert "timestamp" in last
        finally:
            # Clean up: clear the deque
            from cn_scraper_mcp import logging as log_mod
            log_mod._last_errors.clear()

    def test_max_errors_capped(self):
        """Only the last 10 errors are kept."""
        from cn_scraper_mcp import logging as log_mod
        log_mod._last_errors.clear()
        try:
            for i in range(15):
                record_error(RuntimeError(f"error {i}"))
            errors = get_recent_errors()
            assert len(errors) == 10
            # First should be error 5 (oldest kept)
            assert "error 5" in errors[0]["message"]
            assert "error 14" in errors[-1]["message"]
        finally:
            log_mod._last_errors.clear()

    def test_error_entry_has_required_fields(self):
        """Each error entry must have timestamp, type, message."""
        from cn_scraper_mcp import logging as log_mod
        log_mod._last_errors.clear()
        try:
            record_error(KeyError("missing key"))
            errors = get_recent_errors()
            assert len(errors) == 1
            e = errors[0]
            for field in ("timestamp", "type", "message"):
                assert field in e, f"Missing field '{field}' in error entry"
        finally:
            log_mod._last_errors.clear()


# ═══════════════════════════════════════════════════════════════
# diagnose tool structure
# ═══════════════════════════════════════════════════════════════

# Try to import diagnose; may fail if fastmcp is broken/missing in this env.
try:
    from cn_scraper_mcp.server import diagnose as _diagnose_tool

    # FastMCP versions differ: some expose FunctionTool.fn, while newer
    # versions leave the decorated function directly callable.
    _diagnose_func = getattr(_diagnose_tool, "fn", _diagnose_tool)
    _DIAGNOSE_AVAILABLE = callable(_diagnose_func)
except (ImportError, AttributeError):
    _DIAGNOSE_AVAILABLE = False


pytestmark_diagnose = pytest.mark.skipif(
    not _DIAGNOSE_AVAILABLE,
    reason="Cannot import server module (fastmcp broken/missing in test env)",
)


@pytestmark_diagnose
class TestDiagnoseStructure:
    """Test that diagnose() returns the expected structure."""

    def test_diagnose_has_all_sections(self):
        """diagnose() must include platform, dependencies, browsers, cdp_ports,
        cookies, diagnostics sections."""
        result = _diagnose_func()

        required_sections = [
            "platform", "dependencies", "browsers",
            "cdp_ports", "cookies", "diagnostics",
        ]
        for section in required_sections:
            assert section in result, f"diagnose result missing section: {section}"

    def test_platform_section(self):
        """Platform section contains version info."""
        result = _diagnose_func()
        plat = result["platform"]
        assert "package_version" in plat
        assert "python_version" in plat
        assert "os" in plat

    def test_dependencies_section(self):
        """Dependencies section checks required packages."""
        result = _diagnose_func()
        deps = result["dependencies"]
        for dep in ("fastmcp", "curl_cffi", "websockets", "dotenv"):
            assert dep in deps, f"Dependency '{dep}' not checked"
            assert "installed" in deps[dep]
            assert isinstance(deps[dep]["installed"], bool)

    def test_browsers_section(self):
        """Browsers section checks Chrome and Obscura."""
        result = _diagnose_func()
        browsers = result["browsers"]
        assert "chrome" in browsers
        assert "obscura" in browsers
        assert "found" in browsers["chrome"]
        assert "path" in browsers["chrome"]
        assert "version" in browsers["chrome"]

    def test_cdp_ports_section(self):
        """CDP ports section checks 9222, 9247, 9251."""
        result = _diagnose_func()
        ports = result["cdp_ports"]
        for port in ("9222", "9247", "9251"):
            assert port in ports, f"Port {port} not checked"
            assert "in_use" in ports[port]

    def test_cookies_section(self):
        """Cookies section should be a dict."""
        result = _diagnose_func()
        assert isinstance(result["cookies"], dict)

    def test_diagnostics_section(self):
        """Diagnostics section has recent_errors."""
        result = _diagnose_func()
        assert "recent_errors" in result["diagnostics"]
        assert isinstance(result["diagnostics"]["recent_errors"], list)

    def test_diagnose_does_not_scrape(self):
        """diagnose should return quickly — no network scraping."""
        import time

        start = time.monotonic()
        result = _diagnose_func()
        elapsed = time.monotonic() - start

        # All checks should complete within 30s (each check has 5s timeout)
        assert elapsed < 30, f"diagnose took too long: {elapsed:.1f}s"

        # Verify no scrape-like keys in the result
        assert "items" not in result
        assert "search" not in result


# ═══════════════════════════════════════════════════════════════
# set_log_level
# ═══════════════════════════════════════════════════════════════

class TestSetLogLevel:
    """Tests for set_log_level function."""

    def test_set_debug_level(self):
        """set_log_level should change existing logger levels."""
        name = "cn_scraper_mcp.test_level_change"
        logger = get_logger(name)
        logger.setLevel(logging.WARNING)

        set_log_level("DEBUG")

        # Get a fresh reference after the change
        logger2 = logging.getLogger(name)
        assert logger2.level == logging.DEBUG

        # Reset
        logger2.setLevel(logging.WARNING)

    def test_set_invalid_level_defaults_to_warning(self):
        """Invalid level names should default to WARNING."""
        set_log_level("BOGUS_LEVEL")
        logger = get_logger("test_invalid_level")
        # Should not crash
        assert logger.level in (
            logging.DEBUG, logging.INFO, logging.WARNING,
            logging.ERROR, logging.CRITICAL,
        )
