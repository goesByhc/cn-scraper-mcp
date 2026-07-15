"""Unit tests for session.py — SessionManager, CookieSession, ChromeProfileSession, CDPSession.

ALL mocks — no real filesystem, Chrome, or CDP.
NEVER asserts on cookie VALUES — only field names and metadata.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from cn_scraper_mcp.session import (
    COOKIE_DIR,
    DEFAULT_CDP_PORT,
    JD_PROFILE_DIR,
    CDPSession,
    ChromeProfileSession,
    CookieSession,
    SessionManager,
    get_cookie_dir,
    get_cookie_path,
    get_login_signal_cookies,
    get_profile_dir,
    is_profile_platform,
)

# ═══════════════════════════════════════════════════════════════
# CookieSession — path resolution
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionPathResolution:
    """Path resolution: explicit > env var > default."""

    def test_explicit_path_wins(self, monkeypatch):
        """Explicit path passed to __init__ takes highest priority."""
        monkeypatch.setenv("TAOBAO_COOKIES_FILE", "/env/taobao.json")
        explicit = Path("C:/explicit/taobao.json")
        session = CookieSession("taobao", cookies_path=str(explicit))
        assert str(session.resolve_path()) == str(explicit.resolve())

    def test_env_var_when_no_explicit(self, monkeypatch):
        """When no explicit path, env var is used."""
        env_path = Path("C:/env/taobao.json")
        monkeypatch.setenv("TAOBAO_COOKIES_FILE", str(env_path))
        session = CookieSession("taobao")
        assert str(session.resolve_path()) == str(env_path.resolve())

    def test_default_path_fallback(self, monkeypatch):
        """When no explicit path and no env var, use COOKIE_DIR/<name>.json."""
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        session = CookieSession("taobao")
        resolved = session.resolve_path()
        assert resolved.name == "taobao.json"
        assert str(COOKIE_DIR) in str(resolved.parent)

    def test_unknown_platform_raises(self):
        """Passing an unknown platform raises ValueError."""
        with pytest.raises(ValueError, match="Unknown platform"):
            CookieSession("not_a_platform")

    def test_cookie_file_property_is_alias(self):
        """cookie_file property is alias for resolve_path()."""
        session = CookieSession("taobao", cookies_path="/tmp/t.json")
        assert session.cookie_file == session.resolve_path()


# ═══════════════════════════════════════════════════════════════
# CookieSession — field validation
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionValidation:
    """Required-field validation per platform."""

    def test_taobao_all_required_present(self):
        session = CookieSession("taobao", cookies_path="/tmp/t.json")
        missing = session._validate_fields({
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        })
        assert missing == []

    def test_taobao_missing_one_field(self):
        session = CookieSession("taobao", cookies_path="/tmp/t.json")
        missing = session._validate_fields({
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
        })
        assert missing == ["cookie2"]

    def test_taobao_empty_string_treated_as_missing(self):
        session = CookieSession("taobao", cookies_path="/tmp/t.json")
        missing = session._validate_fields({
            "_m_h5_tk": "hidden",
            "_tb_token_": "",
            "cookie2": "hidden",
        })
        assert "_tb_token_" in missing

    def test_taobao_none_treated_as_missing(self):
        session = CookieSession("taobao", cookies_path="/tmp/t.json")
        missing = session._validate_fields({
            "_m_h5_tk": None,
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        })
        assert "_m_h5_tk" in missing

    def test_xiaohongshu_required_fields(self):
        session = CookieSession("xiaohongshu", cookies_path="/tmp/x.json")
        missing = session._validate_fields({"web_session": "hidden", "a1": "hidden"})
        assert missing == []

        missing = session._validate_fields({"web_session": "hidden"})
        assert missing == ["a1"]

    def test_zhihu_required_fields(self):
        session = CookieSession("zhihu", cookies_path="/tmp/z.json")
        missing = session._validate_fields({"z_c0": "hidden"})
        assert missing == []

        missing = session._validate_fields({})
        assert missing == ["z_c0"]

    def test_zsxq_required_fields(self):
        session = CookieSession("zsxq", cookies_path="/tmp/zs.json")
        missing = session._validate_fields({"zsxq_access_token": "hidden"})
        assert missing == []

        missing = session._validate_fields({"zsxq_access_token": ""})
        assert missing == ["zsxq_access_token"]


# ═══════════════════════════════════════════════════════════════
# CookieSession — validate()
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionValidate:
    """validate() returns {valid, reason, ...}."""

    def test_file_not_exists(self, monkeypatch):
        """When file doesn't exist, valid=False."""
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        session = CookieSession("taobao", cookies_path="/nonexistent/file.json")
        result = session.validate()
        assert result["valid"] is False
        assert "not found" in result["reason"]
        assert result["age_hours"] is None
        assert result["stale"] is False

    def test_file_exists_all_fields_valid(self):
        """File exists with all required fields → valid=True."""
        cookie_data = {
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = (
                datetime.datetime.now() - datetime.timedelta(hours=1)
            ).timestamp()

            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.validate()

            assert result["valid"] is True
            assert result["reason"] == ""
            assert result["age_hours"] <= 1.1
            assert result["stale"] is False

    def test_file_exists_missing_fields(self):
        """File exists but missing required fields → valid=False."""
        cookie_data = {"_m_h5_tk": "hidden"}

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):

            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.validate()

            assert result["valid"] is False
            assert "Missing required fields" in result["reason"]

    def test_stale_when_old(self):
        """File older than STALE_HOURS → valid=False, stale=True."""
        from cn_scraper_mcp.auth import STALE_HOURS

        cookie_data = {
            "_m_h5_tk": "hidden", "_tb_token_": "hidden", "cookie2": "hidden",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))
        old_time = (
            datetime.datetime.now() - datetime.timedelta(hours=STALE_HOURS + 10)
        ).timestamp()

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = old_time

            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.validate()

            assert result["stale"] is True
            assert result["valid"] is False
            assert "stale" in result["reason"]
            assert result["age_hours"] >= STALE_HOURS

    def test_unreadable_json_returns_invalid(self):
        """Invalid JSON → valid=False."""
        m_open = mock_open(read_data="not valid json {{{")

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):

            session = CookieSession("taobao", cookies_path="/fake/bad.json")
            result = session.validate()

            assert result["valid"] is False
            assert "unreadable" in result["reason"]

    def test_result_never_contains_cookie_values(self):
        """CRITICAL: validate() output must never contain cookie values."""
        cookie_data = {
            "_m_h5_tk": "secret_token_value",
            "_tb_token_": "another_secret",
            "cookie2": "super_secret_cookie",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()

            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.validate()

            def flatten_strs(d):
                for v in d.values():
                    if isinstance(v, str):
                        yield v
                    elif isinstance(v, list):
                        yield from v

            all_strings = " ".join(flatten_strs(result))
            assert "secret_token_value" not in all_strings
            assert "another_secret" not in all_strings
            assert "super_secret_cookie" not in all_strings


# ═══════════════════════════════════════════════════════════════
# CookieSession — status()
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionStatus:
    """status() extends validate() with platform and session_type."""

    def test_status_includes_platform_and_type(self):
        session = CookieSession("taobao", cookies_path="/nonexistent/file.json")
        with patch.object(Path, "exists", return_value=False):
            result = session.status()
            assert result["platform"] == "taobao"
            assert result["session_type"] == "cookie"
            assert "valid" in result

    def test_status_excludes_cookie_values(self):
        """status() output must never contain cookie values."""
        cookie_data = {
            "_m_h5_tk": "secret_token_value",
            "_tb_token_": "another_secret",
            "cookie2": "super_secret_cookie",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()
            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.status()

            def collect_strs(d):
                for v in d.values():
                    if isinstance(v, str):
                        yield v
                    elif isinstance(v, list):
                        yield from (s for s in v if isinstance(s, str))

            all_text = " ".join(collect_strs(result))
            assert "secret_token_value" not in all_text
            assert "another_secret" not in all_text


# ═══════════════════════════════════════════════════════════════
# CookieSession — delete()
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionDelete:
    """delete() removes the cookie file."""

    def test_delete_existing_file(self):
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "unlink") as mock_unlink:
            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.delete()
            assert result["deleted"] is True
            mock_unlink.assert_called_once()

    def test_delete_nonexistent_file(self):
        with patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "unlink") as mock_unlink:
            session = CookieSession("taobao", cookies_path="/nonexistent/file.json")
            result = session.delete()
            assert result["deleted"] is False
            assert "does not exist" in result["reason"]
            mock_unlink.assert_not_called()

    def test_delete_os_error(self):
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "unlink", side_effect=OSError("Permission denied")):
            session = CookieSession("taobao", cookies_path="/fake/taobao.json")
            result = session.delete()
            assert result["deleted"] is False
            assert "Permission denied" in result["reason"]


# ═══════════════════════════════════════════════════════════════
# CookieSession — login / refresh (delegates to guided_login / CookieHarvester)
# ═══════════════════════════════════════════════════════════════


class TestCookieSessionLogin:
    """login() delegates to guided_login."""

    def test_login_delegates_to_guided_login(self):
        session = CookieSession("taobao", port=9222)
        with patch("cn_scraper_mcp.cookie_harvest.guided_login") as mock_gl:
            mock_gl.return_value = {"platform": "taobao", "status": "ok"}
            result = session.login()
            mock_gl.assert_called_once_with("taobao", port=9222)
            assert result["status"] == "ok"

    def test_refresh_delegates_to_cookie_harvester(self):
        session = CookieSession("taobao", port=9222)
        with patch("cn_scraper_mcp.cookie_harvest.CookieHarvester") as mock_harv_cls:
            mock_harv = MagicMock()
            mock_harv.harvest.return_value = {"platform": "taobao", "status": "ok"}
            mock_harv_cls.return_value = mock_harv

            result = session.refresh()
            mock_harv.harvest.assert_called_once_with("taobao", port=9222)
            assert result["status"] == "ok"

    def test_refresh_error(self):
        from cn_scraper_mcp.cookie_harvest import CookieHarvestError

        session = CookieSession("taobao", port=9222)
        with patch("cn_scraper_mcp.cookie_harvest.CookieHarvester") as mock_harv_cls:
            mock_harv = MagicMock()
            mock_harv.harvest.side_effect = CookieHarvestError("CDP error")
            mock_harv_cls.return_value = mock_harv

            result = session.refresh()
            assert result["status"] == "error"
            assert "CDP error" in result["reason"]


# ═══════════════════════════════════════════════════════════════
# ChromeProfileSession
# ═══════════════════════════════════════════════════════════════


class TestChromeProfileSession:
    """ChromeProfileSession manages persistent Chrome profile dirs."""

    def test_default_profile_dir(self):
        session = ChromeProfileSession()
        assert session.resolve_profile_dir() == JD_PROFILE_DIR

    def test_custom_profile_dir(self):
        session = ChromeProfileSession(profile_dir="/custom/profile")
        assert session.resolve_profile_dir() == Path("/custom/profile").resolve()

    def test_profile_dir_property(self):
        session = ChromeProfileSession()
        assert session.profile_dir == session.resolve_profile_dir()

    def test_validate_profile_exists(self):
        session = ChromeProfileSession(profile_dir="/fake/profile")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mtime = (
                datetime.datetime.now() - datetime.timedelta(hours=1)
            ).timestamp()

            result = session.validate()
            assert result["valid"] is True
            assert result["reason"] == ""
            assert result["age_hours"] <= 1.1
            assert result["stale"] is False

    def test_validate_profile_missing(self):
        session = ChromeProfileSession(profile_dir="/nonexistent/profile")
        with patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "is_dir", return_value=False):
            result = session.validate()
            assert result["valid"] is False
            assert "not found" in result["reason"]
            assert result["age_hours"] is None

    def test_validate_profile_stale(self):
        from cn_scraper_mcp.auth import STALE_HOURS

        session = ChromeProfileSession(profile_dir="/old/profile")
        old_time = (
            datetime.datetime.now() - datetime.timedelta(hours=STALE_HOURS + 5)
        ).timestamp()

        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mtime = old_time

            result = session.validate()
            assert result["stale"] is True
            assert result["valid"] is False
            assert "stale" in result["reason"]

    def test_validate_profile_exists_not_dir(self):
        """If the path exists but is not a directory, treat as missing."""
        session = ChromeProfileSession(profile_dir="/file/not/dir")
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=False):
            result = session.validate()
            assert result["valid"] is False
            assert "not found" in result["reason"]

    def test_status_includes_platform_and_type(self):
        session = ChromeProfileSession(profile_dir="/fake/profile")
        with patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "is_dir", return_value=False):
            result = session.status()
            assert result["platform"] == "jd"
            assert result["session_type"] == "chrome_profile"
            assert "valid" in result

    def test_login_delegates_to_guided_login(self):
        session = ChromeProfileSession(platform="jd", port=9247)
        with patch("cn_scraper_mcp.cookie_harvest.guided_login") as mock_gl:
            mock_gl.return_value = {"platform": "jd", "status": "ok"}
            result = session.login()
            mock_gl.assert_called_once_with("jd", port=9247)
            assert result["status"] == "ok"

    def test_refresh_equals_login(self):
        session = ChromeProfileSession(platform="jd", port=9247)
        with patch("cn_scraper_mcp.cookie_harvest.guided_login") as mock_gl:
            mock_gl.return_value = {"platform": "jd", "status": "ok"}
            result = session.refresh()
            mock_gl.assert_called_once_with("jd", port=9247)
            assert result["status"] == "ok"

    def test_delete_profile_exists(self):
        session = ChromeProfileSession(profile_dir="/fake/profile")
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=False), \
             patch("cn_scraper_mcp.engines.cdp.close_browser"), \
             patch.object(Path, "exists", return_value=True), \
             patch("shutil.rmtree") as mock_rmtree:
            result = session.delete()
            assert result["deleted"] is True
            mock_rmtree.assert_called_once()

    def test_delete_profile_not_exists(self):
        session = ChromeProfileSession(profile_dir="/nonexistent/profile")
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=False), \
             patch.object(Path, "exists", return_value=False), \
             patch("shutil.rmtree") as mock_rmtree:
            result = session.delete()
            assert result["deleted"] is False
            mock_rmtree.assert_not_called()

    def test_delete_closes_browser_first(self):
        session = ChromeProfileSession(profile_dir="/fake/profile", port=9247)
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=True), \
             patch("cn_scraper_mcp.engines.cdp.close_browser") as mock_close, \
             patch.object(Path, "exists", return_value=True), \
             patch("shutil.rmtree"):
            result = session.delete()
            mock_close.assert_called_once_with(9247)
            assert result["deleted"] is True


# ═══════════════════════════════════════════════════════════════
# CDPSession
# ═══════════════════════════════════════════════════════════════


class TestCDPSession:
    """CDPSession manages CDP port, BrowserLock, and metrics."""

    def test_default_port(self):
        session = CDPSession()
        assert session.port == DEFAULT_CDP_PORT

    def test_custom_port(self):
        session = CDPSession(port=9247)
        assert session.port == 9247

    def test_validate_running(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=True):
            result = session.validate()
            assert result["valid"] is True
            assert result["reason"] == ""

    def test_validate_not_running(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=False):
            result = session.validate()
            assert result["valid"] is False
            assert "9222" in result["reason"]

    def test_is_running_property(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=True):
            assert session.is_running is True

        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=False):
            assert session.is_running is False

    def test_get_lock(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.get_browser_lock") as mock_lock:
            lock = MagicMock()
            mock_lock.return_value = lock
            result = session.get_lock()
            mock_lock.assert_called_once_with(9222)
            assert result is lock

    def test_launch_success(self):
        session = CDPSession(port=9222)
        mock_proc = MagicMock()
        with patch("cn_scraper_mcp.engines.cdp.launch_chrome", return_value=mock_proc):
            result = session.launch("/tmp/profile", url="https://example.com")
            assert result is mock_proc
            assert session._lock_holder is True

    def test_launch_failure(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.launch_chrome", return_value=None):
            result = session.launch("/tmp/profile")
            assert result is None
            assert session._lock_holder is False

    def test_close_success(self):
        session = CDPSession(port=9222)
        session._lock_holder = True
        with patch("cn_scraper_mcp.engines.cdp.close_browser", return_value=True):
            result = session.close()
            assert result is True
            assert session._lock_holder is False

    def test_close_failure(self):
        session = CDPSession(port=9222)
        session._lock_holder = True
        with patch("cn_scraper_mcp.engines.cdp.close_browser", return_value=False):
            result = session.close()
            assert result is False
            assert session._lock_holder is False  # still False after attempt

    def test_record_success(self):
        session = CDPSession()
        assert session.last_success is None
        assert session.latency_ms is None

        session.record_success(latency_ms=150.5)
        assert session.last_success is not None
        assert session.latency_ms == 150.5

    def test_record_success_no_latency(self):
        session = CDPSession()
        session.record_success()
        assert session.last_success is not None
        assert session.latency_ms is None

    def test_status(self):
        session = CDPSession(port=9247)
        session.record_success(latency_ms=200.0)
        session._lock_holder = True

        with patch("cn_scraper_mcp.engines.cdp.is_chrome_running", return_value=True):
            result = session.status()
            assert result["valid"] is True
            assert result["port"] == 9247
            assert result["last_success"] is not None
            assert result["latency_ms"] == 200.0
            assert result["lock_holder"] is True

    def test_refresh(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.close_browser", return_value=True):
            result = session.refresh()
            assert result["port"] == 9222
            assert result["restarted"] is True

    def test_delete(self):
        session = CDPSession(port=9222)
        with patch("cn_scraper_mcp.engines.cdp.close_browser", return_value=True):
            result = session.delete()
            assert result["deleted"] is True

        with patch("cn_scraper_mcp.engines.cdp.close_browser", return_value=False):
            result = session.delete()
            assert result["deleted"] is False
            assert "No managed process" in result["reason"]


# ═══════════════════════════════════════════════════════════════
# SessionManager — platform routing
# ═══════════════════════════════════════════════════════════════


class TestSessionManagerRouting:
    """SessionManager routes platforms to correct session type."""

    def test_cookie_platform_gets_cookie_session(self):
        mgr = SessionManager()
        session = mgr._get_session("taobao")
        assert isinstance(session, CookieSession)

    def test_profile_platform_gets_profile_session(self):
        mgr = SessionManager()
        session = mgr._get_session("jd")
        assert isinstance(session, ChromeProfileSession)

    def test_all_cookie_platforms(self):
        """All non-JD platforms should use CookieSession."""
        mgr = SessionManager()
        cookie_platforms = {
            "taobao", "xiaohongshu", "zhihu", "zsxq",
            "pdd", "weibo", "douyin",
        }
        for platform in cookie_platforms:
            session = mgr._get_session(platform)
            assert isinstance(session, CookieSession), \
                f"{platform} should use CookieSession"

    def test_session_reuse(self):
        """Same platform returns the same session instance."""
        mgr = SessionManager()
        s1 = mgr._get_session("taobao")
        s2 = mgr._get_session("taobao")
        assert s1 is s2

    def test_get_cdp_session(self):
        mgr = SessionManager()
        session = mgr.get_cdp_session(port=9222)
        assert isinstance(session, CDPSession)
        assert session.port == 9222

    def test_get_cdp_session_reuse(self):
        mgr = SessionManager()
        s1 = mgr.get_cdp_session(port=9222)
        s2 = mgr.get_cdp_session(port=9222)
        assert s1 is s2


# ═══════════════════════════════════════════════════════════════
# SessionManager — delegation
# ═══════════════════════════════════════════════════════════════


class TestSessionManagerDelegation:
    """SessionManager delegates to the appropriate session."""

    def test_validate_delegates_to_cookie_session(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "validate", return_value={"valid": True, "reason": ""}):
            result = mgr.validate("taobao")
            assert result["valid"] is True

    def test_validate_delegates_to_profile_session(self):
        mgr = SessionManager()
        with patch.object(ChromeProfileSession, "validate",
                          return_value={"valid": True, "reason": ""}):
            result = mgr.validate("jd")
            assert result["valid"] is True

    def test_status_delegates(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "status",
                          return_value={"platform": "taobao", "valid": True, "session_type": "cookie"}):
            result = mgr.status("taobao")
            assert result["platform"] == "taobao"

    def test_delete_delegates(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "delete",
                          return_value={"platform": "taobao", "deleted": True}):
            result = mgr.delete("taobao")
            assert result["deleted"] is True

    def test_login_delegates(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "login",
                          return_value={"platform": "taobao", "status": "ok"}):
            result = mgr.login("taobao")
            assert result["status"] == "ok"

    def test_refresh_delegates(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "refresh",
                          return_value={"platform": "taobao", "status": "ok"}):
            result = mgr.refresh("taobao")
            assert result["status"] == "ok"


# ═══════════════════════════════════════════════════════════════
# SessionManager — status_all / validate_all
# ═══════════════════════════════════════════════════════════════


class TestSessionManagerBulk:
    """status_all and validate_all return results for all platforms."""

    def test_status_all_returns_all_platforms(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "status",
                          return_value={"platform": "x", "valid": True, "session_type": "cookie"}), \
             patch.object(ChromeProfileSession, "status",
                          return_value={"platform": "jd", "valid": True, "session_type": "chrome_profile"}):
            result = mgr.status_all()
            assert "taobao" in result
            assert "xiaohongshu" in result
            assert "zhihu" in result
            assert "zsxq" in result
            assert "jd" in result
            assert "pdd" in result
            assert "weibo" in result
            assert "douyin" in result
            assert len(result) == 8

    def test_validate_all_returns_all_platforms(self):
        mgr = SessionManager()
        with patch.object(CookieSession, "validate",
                          return_value={"valid": True, "reason": ""}), \
             patch.object(ChromeProfileSession, "validate",
                          return_value={"valid": True, "reason": ""}):
            result = mgr.validate_all()
            assert len(result) == 8
            assert all("valid" in v for v in result.values())

    def test_status_all_never_leaks_cookie_values(self):
        """status_all must never leak cookie values."""
        mgr = SessionManager()
        with patch.object(CookieSession, "status",
                          return_value={"platform": "taobao", "valid": True, "session_type": "cookie"}), \
             patch.object(ChromeProfileSession, "status",
                          return_value={"platform": "jd", "valid": True, "session_type": "chrome_profile"}):
            result = mgr.status_all()

            def collect_strings(d, depth=0):
                if depth > 10:
                    return
                if isinstance(d, dict):
                    for v in d.values():
                        yield from collect_strings(v, depth + 1)
                elif isinstance(d, list):
                    for item in d:
                        if isinstance(item, str):
                            yield item
                elif isinstance(d, str):
                    yield d

            all_text = " ".join(collect_strings(result))
            assert "secret" not in all_text.lower()
            assert "token_value" not in all_text.lower()


# ═══════════════════════════════════════════════════════════════
# Module-level helpers
# ═══════════════════════════════════════════════════════════════


class TestModuleHelpers:
    """Module-level convenience functions."""

    def test_get_cookie_dir(self):
        assert get_cookie_dir() == COOKIE_DIR

    def test_get_cookie_path_resolves(self, monkeypatch):
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        path = get_cookie_path("taobao")
        assert path.name == "taobao.json"

    def test_get_profile_dir_jd(self):
        assert get_profile_dir("jd") == JD_PROFILE_DIR

    def test_get_profile_dir_other(self):
        path = get_profile_dir("taobao")
        assert str(path).endswith(".cn_scraper_login_taobao")

    def test_get_login_signal_cookies(self):
        assert get_login_signal_cookies("taobao") == ["_m_h5_tk"]
        assert get_login_signal_cookies("jd") == ["thor", "TrackID"]
        assert get_login_signal_cookies("unknown") == []

    def test_is_profile_platform(self):
        assert is_profile_platform("jd") is True
        assert is_profile_platform("taobao") is False
        assert is_profile_platform("xiaohongshu") is False


# ═══════════════════════════════════════════════════════════════
# Integration: SessionManager drives platform-agnostic flows
# ═══════════════════════════════════════════════════════════════


class TestSessionManagerIntegration:
    """End-to-end session management flows."""

    def test_full_lifecycle_cookie_platform(self):
        """Login → validate → refresh → delete for a cookie platform."""
        mgr = SessionManager()

        with patch.object(CookieSession, "login",
                          return_value={"platform": "taobao", "status": "ok"}), \
             patch.object(CookieSession, "validate",
                          return_value={"valid": True, "reason": ""}), \
             patch.object(CookieSession, "refresh",
                          return_value={"platform": "taobao", "status": "ok"}), \
             patch.object(CookieSession, "delete",
                          return_value={"platform": "taobao", "deleted": True}):

            # Login
            assert mgr.login("taobao")["status"] == "ok"
            # Validate
            assert mgr.validate("taobao")["valid"] is True
            # Refresh
            assert mgr.refresh("taobao")["status"] == "ok"
            # Delete
            assert mgr.delete("taobao")["deleted"] is True

    def test_full_lifecycle_profile_platform(self):
        """Login → validate → refresh → delete for a profile platform."""
        mgr = SessionManager()

        with patch.object(ChromeProfileSession, "login",
                          return_value={"platform": "jd", "status": "ok"}), \
             patch.object(ChromeProfileSession, "validate",
                          return_value={"valid": True, "reason": ""}), \
             patch.object(ChromeProfileSession, "refresh",
                          return_value={"platform": "jd", "status": "ok"}), \
             patch.object(ChromeProfileSession, "delete",
                          return_value={"platform": "jd", "deleted": True}):

            assert mgr.login("jd")["status"] == "ok"
            assert mgr.validate("jd")["valid"] is True
            assert mgr.refresh("jd")["status"] == "ok"
            assert mgr.delete("jd")["deleted"] is True


# ═══════════════════════════════════════════════════════════════
# Backward-compat: cookie_harvest still has COOKIE_DIR
# ═══════════════════════════════════════════════════════════════


class TestBackwardCompat:
    """cookie_harvest module still exposes COOKIE_DIR for existing tests."""

    def test_cookie_harvest_cookie_dir_matches_session(self):
        from cn_scraper_mcp.cookie_harvest import COOKIE_DIR as HARVEST_DIR
        assert HARVEST_DIR == COOKIE_DIR

    def test_cookie_harvest_default_port_matches_session(self):
        from cn_scraper_mcp.cookie_harvest import DEFAULT_PORT
        assert DEFAULT_PORT == DEFAULT_CDP_PORT
