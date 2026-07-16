"""Unit tests for DouyinEngine.search() + hot_list() response parsing.

ALL mocks — no real browser, websocket, or filesystem side effects.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from cn_scraper_mcp.engines.douyin import DouyinEngine
from cn_scraper_mcp.http import HttpClient

# ── Fixtures ──────────────────────────────────────────────────────────


def _make_engine(with_cookies: bool = True) -> DouyinEngine:
    """Build a DouyinEngine with controlled cookies (no file I/O)."""
    eng = DouyinEngine.__new__(DouyinEngine)
    eng.cookies_path = "/fake/path/douyin.json"
    if with_cookies:
        eng.cookies = {"sessionid": "fake_sessionid", "passport_csrf_token": "fake_csrf"}
    else:
        eng.cookies = {}
    eng.port = 9999  # non-conflicting test port
    eng.http = HttpClient(max_retries=0)
    return eng


def _run_result(value):
    """Return an asyncio.run side effect that closes the supplied coroutine."""
    def _side_effect(coro):
        coro.close()
        return value

    return _side_effect


def _run_error(error: Exception):
    """Return an asyncio.run side effect that closes before raising."""
    def _side_effect(coro):
        coro.close()
        raise error

    return _side_effect


def _hot_list_response() -> dict:
    """Realistic douyin hot search API response."""
    return {
        "status_code": 0,
        "data": {
            "word_list": [
                {
                    "word": "华为Mate80发布",
                    "position": 1,
                    "word_record": {"word": "华为Mate80发布", "hot_value": 9800000},
                },
                {
                    "word": "周杰伦新歌",
                    "position": 2,
                    "sentence_info": {"word": "周杰伦新歌", "hot_value": 8500000},
                },
            ],
        },
    }


# ── Hot list tests ─────────────────────────────────────────────────


class TestDouyinHotList:
    """Test douyin_hot_list response parsing."""

    def test_hot_list_parses_correctly(self):
        eng = _make_engine(with_cookies=True)
        eng.http.get_json = MagicMock(return_value=(200, _hot_list_response()))
        result = eng.hot_list()
        assert result["count"] == 2
        assert result["items"][0]["word"] == "华为Mate80发布"
        assert result["items"][0]["hot_value"] == 9800000
        assert result["items"][0]["position"] == 1

    def test_hot_list_needs_cookies(self):
        eng = _make_engine(with_cookies=False)
        result = eng.hot_list()
        assert "error" in result
        assert "热搜需要登录" in result["error"]

    def test_hot_list_handles_http_error(self):
        eng = _make_engine(with_cookies=True)
        eng.http.get_json = MagicMock(return_value=(500, {"error": "server error"}))
        result = eng.hot_list()
        assert "error" in result
        assert "HTTP 500" in result["error"]


# ── Search tests ───────────────────────────────────────────────────


class TestDouyinSearch:
    """Test douyin search behaviour — get_browser_lock now module-level."""

    def test_search_rejects_when_chrome_unavailable(self):
        """search() should return error when ensure_chrome fails inside lock."""
        eng = _make_engine(with_cookies=True)

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=False):
                result = eng.search("华为", limit=5)

        assert result["keyword"] == "华为"
        assert "error" in result

    def test_lock_timeout_returns_error(self):
        """When port lock cannot be acquired, return lock_timeout error."""
        eng = _make_engine(with_cookies=True)

        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            result = eng.search("测试", limit=5)

        assert result["error"] == "lock_timeout"
        assert "端口" in result["hint"]

    def test_search_handles_exception_gracefully(self):
        """search() should catch exceptions and return error dict."""
        eng = _make_engine(with_cookies=True)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=True):
                with patch("asyncio.run", side_effect=_run_error(RuntimeError("async crash"))):
                    result = eng.search("测试", limit=5)

        assert "error" in result

    def test_search_returns_results(self):
        """Successful search should return parsed items with all fields."""
        eng = _make_engine(with_cookies=True)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        mock_items = [
            {"title": "华为发布会", "author": "@华为官方", "views": "50.5万",
             "duration": "03:21", "date": "2026-07-13"},
            {"title": "华为手机评测", "author": "@科技达人", "views": "12.3万",
             "duration": "08:15", "date": "2026-07-12"},
            {"title": "华为P80上手", "author": "@数码控", "views": "8.9万",
             "duration": "05:00", "date": "2026-07-11"},
        ]

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=True):
                with patch("asyncio.run", side_effect=_run_result(mock_items)):
                    result = eng.search("华为", limit=5)

        assert result["keyword"] == "华为"
        assert result["count"] == 3
        assert result["items"][0]["title"] == "华为发布会"
        assert result["items"][0]["author"] == "@华为官方"
        assert result["items"][0]["views"] == "50.5万"
        assert result["items"][0]["duration"] == "03:21"

    def test_search_truncates_by_limit(self):
        """limit should truncate the returned items."""
        eng = _make_engine(with_cookies=True)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        mock_items = [{"title": f"视频{i}", "author": "@test", "views": "1万",
                       "duration": "00:30", "date": "2026-07-01"} for i in range(10)]

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=True):
                with patch("asyncio.run", side_effect=_run_result(mock_items)):
                    result = eng.search("测试", limit=3)

        assert result["count"] == 3
        assert len(result["items"]) == 3

    def test_search_handles_error_dict_from_cdp(self):
        """When _do() returns an error dict, propagate with keyword."""
        eng = _make_engine(with_cookies=True)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        error_response = {"error": "captcha", "hint": "需要验证"}

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=True):
                with patch("asyncio.run", side_effect=_run_result(error_response)):
                    result = eng.search("测试", limit=5)

        assert result["keyword"] == "测试"
        assert result["error"] == "captcha"
        assert result["hint"] == "需要验证"

    def test_cdp_deadline_reachable_from_cdp_send(self):
        """Execute the real CDP command path with a mocked websocket."""
        import urllib.request as _ur

        eng = _make_engine(with_cookies=True)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        # Mock CDP HTTP target discovery — responds with a fake page
        fake_targets = json.dumps([
            {"type": "page", "url": "about:blank",
             "webSocketDebuggerUrl": "ws://127.0.0.1:9999/devtools/page/1"}
        ]).encode()

        class FakeWebSocket:
            def __init__(self):
                self.last_command = None
                self.methods = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def send(self, raw):
                self.last_command = json.loads(raw)
                self.methods.append(self.last_command["method"])

            async def recv(self):
                command = self.last_command
                if command["method"] != "Runtime.evaluate":
                    result = {}
                else:
                    expression = command["params"]["expression"]
                    if "iframe" in expression:
                        value = False
                    elif "innerText.length" in expression:
                        value = "loaded"
                    else:
                        value = json.dumps([{
                            "title": "test", "author": "@a", "views": "1万",
                            "duration": "00:10", "date": "2026-01-01",
                        }])
                    result = {"result": {"value": value}}
                return json.dumps({"id": command["id"], "result": result})

        fake_ws = FakeWebSocket()

        with patch("cn_scraper_mcp.engines.douyin.get_browser_lock", return_value=mock_lock):
            with patch.object(eng, "ensure_chrome", return_value=True):
                with patch.object(_ur, "urlopen") as mock_urlopen:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = fake_targets
                    mock_urlopen.return_value = mock_resp

                    with patch("websockets.connect", return_value=fake_ws):
                        with patch(
                            "cn_scraper_mcp.engines.douyin.asyncio.sleep",
                            new=AsyncMock(return_value=None),
                        ):
                            result = eng.search("测试", limit=5)

        assert result["keyword"] == "测试"
        assert "error" not in result, result
        assert result["count"] == 1
        assert fake_ws.methods == [
            "Page.enable",
            "Page.navigate",
            "Runtime.evaluate",
            "Runtime.evaluate",
            "Runtime.evaluate",
        ]


# ── Constructor tests (no __new__ bypass) ────────────────────────────

class TestDouyinEngineInit:
    """DouyinEngine.__init__() uses CookieFileManager for cookie resolution."""

    def test_init_with_custom_cookies_path(self):
        mock_mgr = MagicMock()
        mock_mgr.load.return_value = {"sessionid": "fake"}
        mock_mgr.resolve_path.return_value = Path("/custom/path.json")

        with patch("cn_scraper_mcp.engines.douyin.CookieFileManager",
                   return_value=mock_mgr) as mock_cls:
            eng = DouyinEngine(cookies_path="/custom/path.json")

            mock_cls.assert_called_once_with("douyin", cookies_path="/custom/path.json")
        assert eng.cookies == {"sessionid": "fake"}
        assert eng.cookies_path == str(Path("/custom/path.json"))

    def test_init_falls_back_to_default(self):
        mock_mgr = MagicMock()
        mock_mgr.load.return_value = {}
        mock_mgr.resolve_path.return_value = (
            Path.home() / ".cn-scraper-cookies" / "douyin.json"
        )

        with patch("cn_scraper_mcp.engines.douyin.CookieFileManager",
                   return_value=mock_mgr) as mock_cls:
            eng = DouyinEngine()

            mock_cls.assert_called_once_with("douyin", cookies_path=None)
        assert eng.cookies == {}

    def test_init_handles_non_object_json(self):
        mock_mgr = MagicMock()
        mock_mgr.load.return_value = {}  # load() returns {} for non-dict JSON
        mock_mgr.resolve_path.return_value = Path("/fake/null.json")

        with patch("cn_scraper_mcp.engines.douyin.CookieFileManager",
                   return_value=mock_mgr):
            eng = DouyinEngine(cookies_path="/fake/null.json")
        assert eng.cookies == {}
