"""Unit tests for ZhihuEngine.search() + hot_list() response parsing.

ALL mocks — no real network, filesystem, or Chrome.

We mock `engine.http.get_json()` directly.
"""

from unittest.mock import Mock

from cn_scraper_mcp.engines.zhihu import ZhihuEngine
from cn_scraper_mcp.http import HttpClient

# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_engine(with_cookies: bool = True) -> ZhihuEngine:
    """Build a ZhihuEngine with controlled cookies (no file I/O)."""
    eng = ZhihuEngine.__new__(ZhihuEngine)
    eng.cookies_path = "/fake/path/zhihu.json"
    if with_cookies:
        eng.cookies = {"z_c0": "fake_z_c0_token", "d_c0": "fake_d_c0_token"}
    else:
        eng.cookies = {}
    # Inject an HttpClient with max_retries=0 (no network retry in tests)
    eng.http = HttpClient(max_retries=0)
    return eng


def _search_response_json():
    """Realistic zhihu search API response JSON."""
    return {
        "data": [
            {
                "object": {
                    "id": 12345,
                    "title": "<em>半导体</em>行业投资趋势分析",
                    "excerpt_title": "半导体行业投资趋势分析",
                    "excerpt": "<p>近年来<em>半导体</em>行业经历了巨大的变革...</p>",
                    "url": "https://www.zhihu.com/question/12345",
                    "type": "answer",
                    "voteup_count": 3200,
                    "comment_count": 180,
                }
            },
            {
                "object": {
                    "id": 12346,
                    "title": "如何看待<em>半导体</em>国产替代",
                    "excerpt": "<p>从产业链角度分析<em>半导体</em>国产替代的现状...</p>",
                    "url": "https://www.zhihu.com/question/12346",
                    "type": "question",
                    "voteup_count": 1500,
                    "comment_count": 95,
                }
            },
            {
                "object": {
                    "id": 12347,
                    "excerpt_title": "<em>半导体</em>ETF投资指南",
                    "excerpt": "",
                    "url": "https://zhuanlan.zhihu.com/p/12347",
                    "type": "article",
                    "voteup_count": 800,
                    "comment_count": 42,
                }
            },
        ],
        "paging": {"is_end": False, "next": "offset=20"},
    }


def _hot_list_response_json():
    """Realistic zhihu hot list API response."""
    return {
        "data": [
            {
                "target": {
                    "title": "华为发布Mate70系列",
                    "url": "https://api.zhihu.com/questions/99999",
                    "excerpt": "华为今日正式发布Mate70系列手机...",
                    "metrics_area": {"text": "1024 万热度"},
                }
            },
            {
                "target": {
                    "title": "OpenAI推出GPT-5",
                    "url": "https://api.zhihu.com/questions/88888",
                    "excerpt": "OpenAI今日发布了GPT-5模型...",
                    "metrics_area": {"text": "899 万热度"},
                }
            },
            {
                "target": {
                    "title": "诺奖经济",
                    "url": "https://api.zhihu.com/questions/77777",
                    "excerpt": "2026年诺贝尔经济学奖揭晓...",
                    "metrics_area": {},
                }
            },
        ]
    }


# ── Tests: search() ────────────────────────────────────────────────────────


class TestZhihuSearch:
    """Test ZhihuEngine.search() response parsing."""

    def test_normal_search_with_cookies(self):
        """With valid cookies, search returns parsed items."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("半导体", limit=10)

        assert "error" not in result
        assert result["keyword"] == "半导体"
        assert len(result["items"]) == 3

        # First item
        item0 = result["items"][0]
        assert "半导体" in item0["title"]
        assert item0["url"] == "https://www.zhihu.com/question/12345"
        assert item0["type"] == "answer"
        assert item0["votes"] == 3200
        assert item0["comments"] == 180
        assert item0["id"] == 12345

        # HTML stripped from title/excerpt
        assert "<em>" not in item0["title"]
        assert "<p>" not in item0["excerpt"]

    def test_search_without_cookies_returns_error_dict(self):
        """No cookies → error dict with 'error' key, no items."""
        engine = _make_engine(with_cookies=False)

        result = engine.search("半导体", limit=10)

        assert "error" in result
        assert "搜索需要登录" in result["error"]
        assert "hint" in result

    def test_no_cookie_http_403_returns_error_dict(self):
        """HTTP 403 when cookies are empty → early return with error dict."""
        engine = _make_engine(with_cookies=False)

        # With no cookies, search() returns early before even calling get_json
        result = engine.search("半导体", limit=10)

        assert "error" in result
        assert "搜索需要登录" in result["error"]

    def test_http_403_with_cookies_returns_generic_error(self):
        """HTTP 403 with cookies → login error (403 triggers login hint)."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(403, {"error": "Forbidden"}))
        result = engine.search("半导体", limit=10)

        assert "error" in result
        assert "搜索需要登录" in result["error"]

    def test_limit_truncates_results(self):
        """limit=2 should return only 2 items."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("半导体", limit=2)

        assert len(result["items"]) == 2

    def test_empty_data_array(self):
        """Empty data array → no items."""
        engine = _make_engine(with_cookies=True)

        resp = {"data": [], "paging": {"is_end": True}}
        engine.http.get_json = Mock(return_value=(200, resp))
        result = engine.search("noresults", limit=10)

        assert result["items"] == []

    def test_object_without_title_uses_excerpt_title(self):
        """When obj.title is None, fall back to excerpt_title."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("半导体", limit=10)

        # Third item has title=None, excerpt_title populated
        item2 = result["items"][2]
        assert "半导体ETF" in item2["title"]

    def test_network_error_returns_error_dict(self):
        """A transport failure → error dict with error details."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(
            return_value=(0, {"error": "Connection failed: Network unreachable"})
        )
        result = engine.search("半导体", limit=10)

        assert "error" in result
        assert "Network unreachable" in result["error"]


# ── Tests: hot_list() ──────────────────────────────────────────────────────


class TestZhihuHotList:
    """Test ZhihuEngine.hot_list() response parsing."""

    def test_normal_hot_list_with_cookies(self):
        """With cookies, hot_list returns parsed trending items."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _hot_list_response_json()))
        result = engine.hot_list()

        assert "error" not in result
        assert len(result["items"]) == 3

        item0 = result["items"][0]
        assert item0["title"] == "华为发布Mate70系列"
        assert item0["url"] == "https://www.zhihu.com/questions/99999"
        assert item0["hot_metric"] == "1024 万热度"

        # URL should have api.zhihu.com replaced with www.zhihu.com
        assert "api.zhihu.com" not in item0["url"]

    def test_hot_list_without_cookies_returns_error_dict(self):
        """No cookies → error dict with 'error' key."""
        engine = _make_engine(with_cookies=False)

        result = engine.hot_list()

        assert "error" in result
        assert "热榜需要登录" in result["error"]

    def test_hot_list_missing_metrics_area(self):
        """Item with no metrics_area → hot_metric should be empty string."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _hot_list_response_json()))
        result = engine.hot_list()

        # Third item has empty metrics_area
        item2 = result["items"][2]
        assert item2["hot_metric"] == ""

    def test_hot_list_empty_data(self):
        """Empty data → empty items list."""
        engine = _make_engine(with_cookies=True)

        resp = {"data": []}
        engine.http.get_json = Mock(return_value=(200, resp))
        result = engine.hot_list()

        assert result["items"] == []
