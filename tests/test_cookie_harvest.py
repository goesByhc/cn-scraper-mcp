"""Unit tests for CDP cookie harvest — CookieHarvester class.

ALL mocks — no real browser, websocket, or filesystem side effects.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cn_scraper_mcp.auth import AUTH_PROFILES
from cn_scraper_mcp.cookie_harvest import (
    CookieHarvester,
    CookieHarvestError,
)

# ── CDP mock helpers ────────────────────────────────────────────


def _mock_cdp_client(cookie_dict: dict[str, str]):
    """Patch CDPClient so connect/get_all_cookies/close return mocked data.

    Returns a context manager that patches CDPClient to return *cookie_dict*
    from get_all_cookies(domain=...).  connect() and close() are no-ops.
    """

    class MockCDPClient:
        def __init__(self, port=None, timeout=None):
            pass

        async def connect(self, url_hint=None):
            pass

        async def close(self):
            pass

        async def get_all_cookies(self, domain=None):
            return cookie_dict

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    return patch(
        "cn_scraper_mcp.cookie_harvest.CDPClient",
        new=MockCDPClient,
    )


# ── Tests: harvest success ───────────────────────────────────────


def test_harvest_taobao_cookies(tmp_path, monkeypatch):
    """Harvest taobao cookies — filters to .taobao.com, saves correctly."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    # get_all_cookies already returns filtered dict — no CDP metadata needed
    taobao_dict = {"_m_h5_tk": "abc123def456", "_tb_token_": "token_xyz789",
                   "cookie2": "val_cookie2", "isg": "val_isg"}

    with _mock_cdp_client(taobao_dict):
        harvester = CookieHarvester()
        result = harvester.harvest("taobao", port=9222)

    assert result["platform"] == "taobao"
    assert result["count"] == 4
    assert result["status"] == "ok"
    assert result["saved_to"] == str(tmp_path / "taobao.json")

    saved_path = tmp_path / "taobao.json"
    assert saved_path.exists()
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert set(saved.keys()) == {"_m_h5_tk", "_tb_token_", "cookie2", "isg"}
    assert isinstance(saved["_m_h5_tk"], str)
    assert isinstance(saved["_tb_token_"], str)


def test_harvest_xiaohongshu_cookies(tmp_path, monkeypatch):
    """Harvest xiaohongshu cookies with correct domain filtering."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    xhs_dict = {"web_session": "xhs_session_value", "a1": "a1_value", "webId": "webid_val"}

    with _mock_cdp_client(xhs_dict):
        harvester = CookieHarvester()
        result = harvester.harvest("xiaohongshu", port=9251)

    assert result["platform"] == "xiaohongshu"
    assert result["count"] == 3
    assert result["status"] == "ok"
    assert result["saved_to"] == str(tmp_path / "xiaohongshu.json")

    saved = json.loads((tmp_path / "xiaohongshu.json").read_text(encoding="utf-8"))
    assert set(saved.keys()) == {"web_session", "a1", "webId"}


# ── Tests: platform validation ──────────────────────────────────


def test_platform_not_supported():
    """Raises ValueError for unsupported platform name."""
    harvester = CookieHarvester()
    with pytest.raises(ValueError, match="Unsupported platform"):
        harvester.harvest("unsupported_platform", port=9222)


def test_profile_platform_requires_guided_login(tmp_path, monkeypatch):
    """Direct harvesting must not create an unused JSON file for JD."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    result = CookieHarvester().harvest("jd", port=9247)

    assert result["status"] == "profile_required"
    assert result["saved_to"] is None
    assert "guided_login" in result["hint"]
    assert not (tmp_path / "jd.json").exists()


def test_platform_registry_completeness():
    """All expected platforms are in AUTH_PROFILES."""
    expected = {"taobao", "xiaohongshu", "zhihu", "zsxq", "jd", "pdd", "weibo", "douyin"}
    assert set(AUTH_PROFILES.keys()) == expected


def test_platform_domains_correct():
    """Each platform maps to the correct domain."""
    assert AUTH_PROFILES["taobao"].cookie_domain == ".taobao.com"
    assert AUTH_PROFILES["xiaohongshu"].cookie_domain == ".xiaohongshu.com"
    assert AUTH_PROFILES["zhihu"].cookie_domain == ".zhihu.com"
    assert AUTH_PROFILES["zsxq"].cookie_domain == ".zsxq.com"
    assert AUTH_PROFILES["jd"].cookie_domain == ".jd.com"
    assert AUTH_PROFILES["pdd"].cookie_domain == ".yangkeduo.com"


# ── Tests: CDP connection failure ───────────────────────────────


def test_cdp_connection_refused():
    """CookieHarvestError when CDP port is not listening."""
    with patch(
        "cn_scraper_mcp.cookie_harvest.CDPClient.connect",
        side_effect=Exception("Cannot reach CDP on port 9222"),
    ):
        harvester = CookieHarvester()
        with pytest.raises(CookieHarvestError, match="CDP error"):
            harvester.harvest("taobao", port=9999)


def test_no_page_targets():
    """CookieHarvestError when no page targets are available."""
    with patch(
        "cn_scraper_mcp.cookie_harvest.CDPClient.connect",
        side_effect=Exception("No page target found"),
    ):
        harvester = CookieHarvester()
        with pytest.raises(CookieHarvestError, match="CDP error"):
            harvester.harvest("taobao", port=9222)


def test_websocket_connection_failure():
    """CookieHarvestError when websocket connection fails."""
    with patch(
        "cn_scraper_mcp.cookie_harvest.CDPClient.connect",
        side_effect=Exception("Connection reset"),
    ):
        harvester = CookieHarvester()
        with pytest.raises(CookieHarvestError, match="CDP error"):
            harvester.harvest("taobao", port=9222)


# ── Tests: cookie save path ─────────────────────────────────────


def test_cookies_saved_to_correct_path(tmp_path, monkeypatch):
    """Cookies are saved to ~/.cn-scraper-cookies/<platform>.json."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    xhs_dict = {"web_session": "xhs_session_value", "a1": "a1_value", "webId": "webid_val"}

    with _mock_cdp_client(xhs_dict):
        harvester = CookieHarvester()
        result = harvester.harvest("xiaohongshu", port=9222)

    assert result["saved_to"] == str(tmp_path / "xiaohongshu.json")
    assert (tmp_path / "xiaohongshu.json").exists()


# ── Tests: no cookies for platform ──────────────────────────────


def test_harvest_no_matching_cookies(tmp_path, monkeypatch):
    """When no cookies match the domain, count is 0 and file is not saved."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    with _mock_cdp_client({}):
        harvester = CookieHarvester()
        result = harvester.harvest("taobao", port=9222)

    assert result["platform"] == "taobao"
    assert result["count"] == 0
    assert result["status"] == "empty"  # empty harvest, file NOT overwritten
    assert result["saved_to"] is None

    # Old file should NOT exist (never saved)
    assert not (tmp_path / "taobao.json").exists()


def test_empty_login_signal_preserves_existing_cookies(tmp_path, monkeypatch):
    """An empty login-signal value must not overwrite a valid cookie cache."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )
    save_path = tmp_path / "taobao.json"
    save_path.write_text(
        json.dumps({"_m_h5_tk": "existing-token", "cookie2": "existing-cookie"}),
        encoding="utf-8",
    )

    with _mock_cdp_client({"_m_h5_tk": "", "cna": "anonymous-cookie"}):
        result = CookieHarvester().harvest("taobao", port=9222)

    assert result["status"] == "partial"
    assert result["saved_to"] is None
    assert json.loads(save_path.read_text(encoding="utf-8")) == {
        "_m_h5_tk": "existing-token",
        "cookie2": "existing-cookie",
    }


# ── Tests: HttpOnly cookies are captured ────────────────────────


def test_httponly_cookies_harvested(tmp_path, monkeypatch):
    """HttpOnly cookies (invisible to JS) are harvested via CDP."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    cookie_dict = {
        "_m_h5_tk": "tk_signal_for_harvest",
        "httponly_secret": "secret_value",
        "js_visible": "visible_value",
    }

    with _mock_cdp_client(cookie_dict):
        harvester = CookieHarvester()
        result = harvester.harvest("taobao", port=9222)

    assert result["count"] == 3
    saved = json.loads((tmp_path / "taobao.json").read_text(encoding="utf-8"))
    # Both values are flat strings (engine-compatible)
    assert saved["_m_h5_tk"] == "tk_signal_for_harvest"
    assert saved["httponly_secret"] == "secret_value"
    assert saved["js_visible"] == "visible_value"
