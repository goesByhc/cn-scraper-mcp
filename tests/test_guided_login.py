"""Unit tests for guided_login flow.

ALL mocks — no real browser, no Chrome, no filesystem side effects.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.cookie_harvest import (
    CookieHarvester,
    CookieHarvestError,
    guided_login,
)

# ── Helpers ─────────────────────────────────────────────────────


def _fake_raw_cookies(platform: str, logged_in: bool = True) -> dict[str, str]:
    """Return a mock cookie dict — with or without signal cookies."""
    all_cookies = {
        "taobao": {"_m_h5_tk": "tk_abc", "cookie2": "val2", "cna": "cna_val"},
        "xiaohongshu": {"web_session": "sess_123", "a1": "a1_val"},
        "zhihu": {"z_c0": "zc0_val", "d_c0": "dc0_val"},
        "jd": {"thor": "jd_thor_val", "TrackID": "track_abc"},
        "weibo": {"SUB": "sub_token", "SUBP": "subp_val"},
        "douyin": {"sessionid": "sess_dy"},
    }
    anon = {
        "taobao": {"cna": "anon_cna"},
        "xiaohongshu": {"a1": "anon_a1"},
        "zhihu": {},
        "jd": {"__jda": "anon_jda"},
        "weibo": {},
        "douyin": {},
    }
    return all_cookies.get(platform, {}) if logged_in else anon.get(platform, {})


def _mock_launch_success():
    """Return a MagicMock that looks like a subprocess.Popen."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    return mock_proc


# ── Tests ──────────────────────────────────────────────────────


class TestGuidedLoginSuccess:
    """guided_login with valid login signal cookies."""

    def test_taobao_login_detected_and_saved(self):
        """Taobao login: _m_h5_tk present -> harvest and save."""
        raw = _fake_raw_cookies("taobao", logged_in=True)

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value=raw):
                        with patch.object(Path, "mkdir"):
                            with patch("builtins.open") as mock_open:
                                with patch.object(Path, "replace"):
                                    result = guided_login("taobao", port=9222, timeout=2)

        assert result["status"] == "ok"
        assert result["method"] == "guided_login"
        assert result["count"] == 3
        mock_open.assert_called_once()

    def test_weibo_login_detected(self):
        """Weibo login: SUB present -> success."""
        raw = _fake_raw_cookies("weibo", logged_in=True)

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value=raw):
                        with patch.object(Path, "mkdir"):
                            with patch("builtins.open"):
                                with patch.object(Path, "replace"):
                                    result = guided_login("weibo", port=9222, timeout=2)

        assert result["status"] == "ok"
        assert result["count"] == 2


class TestGuidedLoginAnonymous:
    """Guided login when user hasn't logged in yet (anonymous cookies)."""

    def test_anonymous_cookies_not_saved(self):
        """Anonymous cookies should not pass signal check -> timeout."""
        raw = _fake_raw_cookies("taobao", logged_in=False)

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value=raw) as mock_hr:
                        result = guided_login("taobao", port=9222, timeout=1)

        assert result["status"] == "timeout"
        mock_hr.assert_called_with("taobao", port=9222)

    def test_empty_cookies_continue_polling(self):
        """Empty harvest -> continue polling, don't crash."""

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value={}):
                        result = guided_login("zhihu", port=9222, timeout=1)

        assert result["status"] == "timeout"

    def test_empty_login_signal_does_not_complete_login(self):
        """A present signal cookie with an empty value is still anonymous."""
        raw = {"_m_h5_tk": "", "cna": "anonymous-cookie"}

        with patch("cn_scraper_mcp.cookie_harvest.GUIDED_LOGIN_POLL", 0.01):
            with patch(
                "cn_scraper_mcp.cookie_harvest.launch_chrome",
                return_value=_mock_launch_success(),
            ):
                with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                    with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                        with patch.object(CookieHarvester, "harvest_raw", return_value=raw):
                            result = guided_login("taobao", port=9222, timeout=0.03)

        assert result["status"] == "timeout"


class TestGuidedLoginJDProfile:
    """JD platform uses persistent Chrome profile — not cookie JSON."""

    def test_jd_uses_correct_profile_path(self):
        """JD guided_login should use ~/.jd_login_profile."""
        raw = _fake_raw_cookies("jd", logged_in=True)

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome") as mock_launch:
            mock_launch.return_value = _mock_launch_success()
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value=raw):
                        result = guided_login("jd", timeout=2)

        assert result["status"] == "ok"
        assert "jd_login_profile" in result["saved_to"]
        assert "JDEngine" in result["hint"]
        args, kwargs = mock_launch.call_args
        profile = kwargs.get("profile_dir") or (args[1] if len(args) > 1 else "")
        assert ".jd_login_profile" in str(profile)

    def test_jd_returns_profile_hint(self):
        """JD result should explain profile-based login."""
        raw = _fake_raw_cookies("jd", logged_in=True)

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value=raw):
                        result = guided_login("jd", timeout=2)

        assert "jd_search" in result["hint"]


class TestGuidedLoginLaunchFailure:
    """When Chrome fails to start, don't enter polling loop."""

    def test_launch_chrome_returns_none(self):
        """Should return error immediately, not poll for 120s."""

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=None):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    result = guided_login("taobao", port=9222, timeout=120)

        assert result["status"] == "error"
        assert "Chrome 启动失败" in result["hint"]


class TestGuidedLoginTimeout:
    """Timeout when user never logs in."""

    def test_timeout_returns_clear_message(self):
        """Timeout should return status=timeout with helpful hint."""

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", return_value={}):
                        result = guided_login("taobao", port=9222, timeout=1)

        assert result["status"] == "timeout"
        assert result["count"] == 0
        assert result["saved_to"] is None

    def test_cdp_errors_continue_polling(self):
        """CookieHarvestError should not stop polling."""

        with patch("cn_scraper_mcp.cookie_harvest.launch_chrome", return_value=_mock_launch_success()):
            with patch("cn_scraper_mcp.cookie_harvest.is_chrome_running", return_value=False):
                with patch("cn_scraper_mcp.cookie_harvest.close_browser"):
                    with patch.object(CookieHarvester, "harvest_raw", side_effect=CookieHarvestError("fail")):
                        result = guided_login("taobao", port=9222, timeout=1)

        assert result["status"] == "timeout"


class TestGuidedLoginInvalidPlatform:
    """Reject unsupported platforms."""

    def test_invalid_platform_raises(self):
        with pytest.raises(ValueError, match="Unsupported platform"):
            guided_login("google")
