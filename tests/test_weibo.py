"""Unit tests for WeiboEngine.search() + hot_list() response parsing.

ALL mocks — no real network, filesystem, or Chrome.

We mock `engine.http.get_json()` directly.
"""

from unittest.mock import Mock

from cn_scraper_mcp.engines.weibo import WeiboEngine, _clean_html
from cn_scraper_mcp.http import HttpClient


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_engine(with_cookies: bool = True) -> WeiboEngine:
    """Build a WeiboEngine with controlled cookies (no file I/O)."""
    eng = WeiboEngine.__new__(WeiboEngine)
    eng.cookies_path = "/fake/path/weibo.json"
    if with_cookies:
        eng.cookies = {"SUB": "fake_sub_token_value"}
    else:
        eng.cookies = {}
    eng.http = HttpClient(max_retries=0)
    return eng


def _search_response_json() -> dict:
    """Realistic m.weibo.cn search API response."""
    return {
        "ok": 1,
        "data": {
            "cards": [
                {
                    "card_type": 9,
                    "mblog": {
                        "mid": "5123456789012345",
                        "text": "华为Mate70真是太<em>好用了</em>！<br />拍照效果惊艳",
                        "user": {"screen_name": "数码爱好者"},
                        "attitudes_count": 2300,
                        "comments_count": 156,
                        "reposts_count": 89,
                        "created_at": "07-12",
                    },
                },
                {
                    "card_type": 9,
                    "mblog": {
                        "mid": "5123456789012346",
                        "text": "分享一下<a href='/n/华为终端'>@华为终端</a>的新品体验",
                        "user": {"screen_name": "科技小明"},
                        "attitudes_count": 1200,
                        "comments_count": 45,
                        "reposts_count": 32,
                        "created_at": "07-11",
                    },
                },
                {
                    "card_type": 11,  # Not an mblog — should be skipped
                    "card_group": [],
                },
                {
                    "card_type": 9,
                    "mblog": {
                        "id": "5123456789012347",  # uses 'id' instead of 'mid'
                        "text": "华为P70降价了",
                        "user": {"screen_name": "数码爆料站"},
                        "attitudes_count": 3400,
                        "comments_count": 210,
                        "reposts_count": 150,
                        "created_at": "07-13",
                    },
                },
            ]
        },
    }


def _search_not_logged_in_response() -> dict:
    """Weibo API response when not logged in."""
    return {
        "ok": -100,
        "url": "https://passport.weibo.com/sso/signin?entry=wapsso...",
    }


def _hot_list_response_json() -> dict:
    """Realistic weibo.com/ajax/side/hotSearch response."""
    return {
        "ok": 1,
        "data": {
            "realtime": [
                {
                    "word": "中国首个禁售燃油车省份确认",
                    "realpos": 1,
                    "num": 1105077,
                    "label_name": "爆",
                    "note": "中国首个禁售燃油车省份确认",
                    "topic_flag": 1,
                },
                {
                    "word": "沈阳百年一遇暴雨",
                    "realpos": 2,
                    "num": 1100357,
                    "label_name": "热",
                    "note": "沈阳百年一遇暴雨",
                    "topic_flag": 1,
                },
                {
                    "word": "华为Mate70发布会",
                    "realpos": 3,
                    "num": 980000,
                    "label_name": "",
                    "note": "华为Mate70系列新品发布",
                    "topic_flag": 1,
                },
            ],
            "hotgov": {
                "name": "#习近平将出席2026世界人工智能大会开幕式#",
                "word": "#习近平将出席2026世界人工智能大会开幕式#",
                "url": "http://weibo.com/1699432410/R8unx97b6",
                "note": "#习近平将出席2026世界人工智能大会开幕式#",
            },
        },
    }


# ── Tests: _clean_html ──────────────────────────────────────────────────


class TestCleanHtml:
    """Test HTML cleaning utility."""

    def test_strips_basic_tags(self):
        assert _clean_html("Hello <b>World</b>") == "Hello World"

    def test_strips_br_tags(self):
        assert _clean_html("Line 1<br />Line 2") == "Line 1Line 2"

    def test_strips_em_tags(self):
        assert _clean_html("华为<em>Mate70</em>真好") == "华为Mate70真好"

    def test_strips_links(self):
        text = '分享<a href="/n/华为终端">@华为终端</a>新品'
        assert _clean_html(text) == "分享@华为终端新品"

    def test_none_returns_empty(self):
        assert _clean_html(None) == ""

    def test_empty_string(self):
        assert _clean_html("") == ""


# ── Tests: search() ──────────────────────────────────────────────────────


class TestWeiboSearch:
    """Test WeiboEngine.search() response parsing."""

    def test_normal_search_with_cookies(self):
        """With valid cookies, search returns parsed items."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("华为", limit=10)

        assert "error" not in result
        assert result["keyword"] == "华为"
        assert result["count"] == 3

        # First item
        item0 = result["items"][0]
        assert item0["id"] == "5123456789012345"
        assert "华为" in item0["text"]
        assert "<em>" not in item0["text"]  # HTML stripped
        assert "<br" not in item0["text"]
        assert item0["user"] == "数码爱好者"
        assert item0["attitudes"] == 2300
        assert item0["comments"] == 156
        assert item0["reposts"] == 89
        assert item0["url"] == "https://m.weibo.cn/detail/5123456789012345"

    def test_search_without_cookies_returns_error(self):
        """No cookies → error dict with hint."""
        engine = _make_engine(with_cookies=False)

        result = engine.search("华为", limit=10)

        assert "error" in result
        assert "搜索需要登录" in result["error"]
        assert "hint" in result

    def test_api_returns_ok_negative_100(self):
        """API returns ok:-100 → login required error."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_not_logged_in_response()))
        result = engine.search("华为", limit=10)

        assert "error" in result
        assert "搜索需要登录" in result["error"]

    def test_limit_truncates_results(self):
        """limit=2 should return only 2 items."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("华为", limit=2)

        assert result["count"] == 2
        assert len(result["items"]) == 2

    def test_network_error_returns_error_dict(self):
        """Transport failure → error dict."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(
            return_value=(0, {"error": "Connection failed: timeout"})
        )
        result = engine.search("华为", limit=10)

        assert "error" in result
        assert "timeout" in result["error"]

    def test_skips_non_mblog_cards(self):
        """card_type != 9 should be skipped."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("华为", limit=10)

        # card_type 11 is not mblog — should not appear
        ids = [item["id"] for item in result["items"]]
        assert "no-id" not in ids  # check all items have valid ids
        assert len(result["items"]) == 3  # 3 card_type=9, 1 card_type=11 skipped

    def test_uses_id_fallback_when_mid_missing(self):
        """When mid is absent, falls back to id field."""
        engine = _make_engine(with_cookies=True)

        engine.http.get_json = Mock(return_value=(200, _search_response_json()))
        result = engine.search("华为", limit=10)

        # Third item has 'id' not 'mid'
        item2 = result["items"][2]
        assert item2["id"] == "5123456789012347"

    def test_http_400_returns_error(self):
        engine = _make_engine(with_cookies=True)
        engine.http.get_json = Mock(return_value=(400, {"error": "Bad request"}))
        result = engine.search("test", limit=10)
        assert "error" in result


# ── Tests: hot_list() ────────────────────────────────────────────────────


class TestWeiboHotList:
    """Test WeiboEngine.hot_list() response parsing."""

    def test_normal_hot_list(self):
        """hot_list returns parsed trending items."""
        engine = _make_engine(with_cookies=False)  # hot list doesn't need cookies

        engine.http.get_json = Mock(return_value=(200, _hot_list_response_json()))
        result = engine.hot_list()

        assert "error" not in result
        assert result["count"] == 3
        assert len(result["items"]) == 3

        item0 = result["items"][0]
        assert item0["word"] == "中国首个禁售燃油车省份确认"
        assert item0["rank"] == 1
        assert item0["num"] == 1105077
        assert item0["label"] == "爆"
        assert "s.weibo.com" in item0["url"]

        # hotgov
        assert result["hotgov"] is not None
        assert "习近平" in result["hotgov"]["name"]

    def test_hot_list_network_error(self):
        """Network error → error dict."""
        engine = _make_engine(with_cookies=False)

        engine.http.get_json = Mock(
            return_value=(0, {"error": "Connection refused"})
        )
        result = engine.hot_list()

        assert "error" in result
        assert "refused" in result["error"]

    def test_hot_list_empty_realtime(self):
        """Empty realtime array → no items."""
        engine = _make_engine(with_cookies=False)

        resp = {"ok": 1, "data": {"realtime": []}}
        engine.http.get_json = Mock(return_value=(200, resp))
        result = engine.hot_list()

        assert result["items"] == []
        assert result["count"] == 0

    def test_hot_list_no_hotgov(self):
        """No hotgov field → null."""
        engine = _make_engine(with_cookies=False)

        resp = {
            "ok": 1,
            "data": {
                "realtime": [
                    {"word": "test", "realpos": 1, "num": 100, "label_name": "", "note": ""}
                ]
            },
        }
        engine.http.get_json = Mock(return_value=(200, resp))
        result = engine.hot_list()

        assert result["hotgov"] is None

    def test_hot_list_http_500(self):
        engine = _make_engine(with_cookies=False)
        engine.http.get_json = Mock(return_value=(500, {"error": "Internal error"}))
        result = engine.hot_list()
        assert "error" in result
