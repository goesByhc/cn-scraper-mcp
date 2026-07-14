"""Unit tests for CDP cookie harvest — CookieHarvester class.

ALL mocks — no real browser, websocket, or filesystem side effects.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cn_scraper_mcp.cookie_harvest import (
    PLATFORM_DOMAINS,
    CookieHarvester,
    CookieHarvestError,
)

# ── Cookie fixtures ──────────────────────────────────────────────


def _make_taobao_cookies() -> list[dict]:
    """Mock CDP cookie list — Taobao cookies + noise from other domains."""
    return [
        {
            "name": "_m_h5_tk",
            "value": "abc123def456",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "_tb_token_",
            "value": "token_xyz789",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": True,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "cookie2",
            "value": "val_cookie2",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": False,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "isg",
            "value": "val_isg",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": True,
            "secure": False,
            "sameSite": "Lax",
        },
        # Noise — different domain, should be filtered out
        {
            "name": "web_session",
            "value": "xhs_session",
            "domain": ".xiaohongshu.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "tracking",
            "value": "track_val",
            "domain": ".doubleclick.net",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
    ]


def _make_xiaohongshu_cookies() -> list[dict]:
    """Mock CDP cookie list — Xiaohongshu cookies."""
    return [
        {
            "name": "web_session",
            "value": "xhs_session_value",
            "domain": ".xiaohongshu.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "a1",
            "value": "a1_value",
            "domain": ".xiaohongshu.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "webId",
            "value": "webid_val",
            "domain": ".xiaohongshu.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
    ]


# ── CDP mock helpers ────────────────────────────────────────────


def _mock_http_json(targets: list[dict]):
    """Patch urllib.request.urlopen to return page targets."""
    raw = json.dumps(targets).encode("utf-8")

    def _urlopen(url, **kwargs):
        m = MagicMock()
        m.read.return_value = raw
        return m

    return patch(
        "cn_scraper_mcp.cookie_harvest.urllib.request.urlopen",
        side_effect=_urlopen,
    )


def _mock_websocket(cookies: list[dict]):
    """Patch websockets.connect to return a mock async context manager.

    The returned mock websocket responds to CDP commands:
    - msg 1 → Network.enable response
    - msg 2 → Network.getAllCookies with the given cookies

    websockets.connect() is used as ``async with websockets.connect(...)``
    (without await), so the patched callable must return an object with
    __aenter__ / __aexit__, NOT a coroutine.
    """

    def _connect(ws_url, **kwargs):
        ws = MagicMock()
        ws.__aenter__ = AsyncMock(return_value=ws)
        ws.__aexit__ = AsyncMock(return_value=None)

        # Simulate CDP responses — Network.enable then Network.getAllCookies
        responses = [
            json.dumps({"id": 1, "result": {}}),  # Network.enable response
            json.dumps({"id": 2, "result": {"cookies": cookies}}),  # getAllCookies
        ]
        recv_idx = [0]  # mutable so the closure below can mutate it

        async def _recv():
            idx = recv_idx[0]
            if idx < len(responses):
                recv_idx[0] = idx + 1
                return responses[idx]
            import asyncio
            await asyncio.sleep(10)
            return "{}"

        ws.recv = _recv
        ws.send = AsyncMock()
        ws.close = AsyncMock()
        return ws

    return patch(
        "cn_scraper_mcp.cookie_harvest.websockets.connect",
        side_effect=_connect,
    )


# ── Tests: harvest success ───────────────────────────────────────


def test_harvest_taobao_cookies(tmp_path, monkeypatch):
    """Harvest taobao cookies — filters to .taobao.com, saves correctly."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    taobao_cookies = _make_taobao_cookies()
    page_targets = [
        {"type": "page", "url": "https://www.taobao.com/",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/ABC"}
    ]

    with _mock_http_json(page_targets):
        with _mock_websocket(taobao_cookies):
            harvester = CookieHarvester()
            result = harvester.harvest("taobao", port=9222)

    assert result["platform"] == "taobao"
    assert result["count"] == 4  # 4 taobao cookies, 2 noise filtered out
    assert result["status"] == "ok"
    assert result["saved_to"] == str(tmp_path / "taobao.json")

    # Verify saved file contents
    saved_path = tmp_path / "taobao.json"
    assert saved_path.exists()
    saved = json.loads(saved_path.read_text(encoding="utf-8"))

    # Should only contain taobao cookies
    assert set(saved.keys()) == {"_m_h5_tk", "_tb_token_", "cookie2", "isg"}
    # Values are flat strings (engine-compatible format), not metadata dicts
    assert isinstance(saved["_m_h5_tk"], str)
    assert isinstance(saved["_tb_token_"], str)


def test_harvest_xiaohongshu_cookies(tmp_path, monkeypatch):
    """Harvest xiaohongshu cookies with correct domain filtering."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    xhs_cookies = _make_xiaohongshu_cookies()
    page_targets = [
        {"type": "page", "url": "https://www.xiaohongshu.com/explore",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9251/devtools/page/DEF"}
    ]

    with _mock_http_json(page_targets):
        with _mock_websocket(xhs_cookies):
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


def test_platform_registry_completeness():
    """All expected platforms are in PLATFORM_DOMAINS."""
    expected = {"taobao", "xiaohongshu", "zhihu", "zsxq", "jd", "pdd", "weibo", "douyin"}
    assert set(PLATFORM_DOMAINS.keys()) == expected


def test_platform_domains_correct():
    """Each platform maps to the correct domain."""
    assert PLATFORM_DOMAINS["taobao"] == ".taobao.com"
    assert PLATFORM_DOMAINS["xiaohongshu"] == ".xiaohongshu.com"
    assert PLATFORM_DOMAINS["zhihu"] == ".zhihu.com"
    assert PLATFORM_DOMAINS["zsxq"] == ".zsxq.com"
    assert PLATFORM_DOMAINS["jd"] == ".jd.com"
    assert PLATFORM_DOMAINS["pdd"] == ".yangkeduo.com"


# ── Tests: CDP connection failure ───────────────────────────────


def test_cdp_connection_refused():
    """CookieHarvestError when CDP port is not listening."""
    with patch(
        "cn_scraper_mcp.cookie_harvest.urllib.request.urlopen",
        side_effect=OSError("Connection refused"),
    ):
        harvester = CookieHarvester()
        with pytest.raises(CookieHarvestError, match="Cannot reach CDP"):
            harvester.harvest("taobao", port=9999)


def test_no_page_targets():
    """CookieHarvestError when /json returns no page targets."""
    targets = [
        {"type": "service_worker", "url": "sw://..."},
        {"type": "other", "url": "..."},
    ]
    raw = json.dumps(targets).encode("utf-8")

    def _urlopen(url, **kwargs):
        m = MagicMock()
        m.read.return_value = raw
        return m

    with patch(
        "cn_scraper_mcp.cookie_harvest.urllib.request.urlopen",
        side_effect=_urlopen,
    ):
        harvester = CookieHarvester()
        with pytest.raises(CookieHarvestError, match="No page target found"):
            harvester.harvest("taobao", port=9222)


def test_websocket_connection_failure():
    """CookieHarvestError when websocket connect fails."""
    page_targets = [
        {"type": "page", "url": "about:blank",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/XYZ"}
    ]

    with _mock_http_json(page_targets):
        with patch(
            "cn_scraper_mcp.cookie_harvest.websockets.connect",
            side_effect=OSError("Connection reset"),
        ):
            harvester = CookieHarvester()
            with pytest.raises(CookieHarvestError, match="WebSocket connection failed"):
                harvester.harvest("taobao", port=9222)


# ── Tests: cookie save path ─────────────────────────────────────


def test_cookies_saved_to_correct_path(tmp_path, monkeypatch):
    """Cookies are saved to ~/.cn-scraper-cookies/<platform>.json."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    cookies = _make_xiaohongshu_cookies()
    page_targets = [
        {"type": "page", "url": "https://www.xiaohongshu.com/",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/GHI"}
    ]

    with _mock_http_json(page_targets):
        with _mock_websocket(cookies):
            harvester = CookieHarvester()
            result = harvester.harvest("xiaohongshu", port=9222)

    assert result["saved_to"] == str(tmp_path / "xiaohongshu.json")
    assert (tmp_path / "xiaohongshu.json").exists()


# ── Tests: no cookies for platform ──────────────────────────────


def test_harvest_no_matching_cookies(tmp_path, monkeypatch):
    """When no cookies match the domain, count is 0 and file is still saved."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    # Only noise cookies, none matching taobao
    cookies = [
        {
            "name": "tracking", "value": "x",
            "domain": ".doubleclick.net", "path": "/",
            "httpOnly": False, "secure": False, "sameSite": "Lax",
        }
    ]
    page_targets = [
        {"type": "page", "url": "about:blank",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/JKL"}
    ]

    with _mock_http_json(page_targets):
        with _mock_websocket(cookies):
            harvester = CookieHarvester()
            result = harvester.harvest("taobao", port=9222)

    assert result["platform"] == "taobao"
    assert result["count"] == 0
    assert result["status"] == "empty"  # empty harvest, file NOT overwritten
    assert result["saved_to"] is None

    # Old file should NOT exist (never saved)
    assert not (tmp_path / "taobao.json").exists()


# ── Tests: HttpOnly cookies are captured ────────────────────────


def test_httponly_cookies_harvested(tmp_path, monkeypatch):
    """HttpOnly cookies (invisible to JS) are harvested via CDP."""
    monkeypatch.setattr(
        "cn_scraper_mcp.cookie_harvest.COOKIE_DIR", tmp_path
    )

    cookies = [
        {
            "name": "_m_h5_tk",
            "value": "tk_signal_for_harvest",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
        {
            "name": "httponly_secret",
            "value": "secret_value",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Strict",
        },
        {
            "name": "js_visible",
            "value": "visible_value",
            "domain": ".taobao.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        },
    ]
    page_targets = [
        {"type": "page", "url": "https://taobao.com/",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/MNO"}
    ]

    with _mock_http_json(page_targets):
        with _mock_websocket(cookies):
            harvester = CookieHarvester()
            result = harvester.harvest("taobao", port=9222)

    assert result["count"] == 3
    saved = json.loads((tmp_path / "taobao.json").read_text(encoding="utf-8"))
    # Both values are flat strings (engine-compatible)
    assert saved["_m_h5_tk"] == "tk_signal_for_harvest"
    assert saved["httponly_secret"] == "secret_value"
    assert saved["js_visible"] == "visible_value"
