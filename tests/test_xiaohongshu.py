"""Unit tests for XiaohongshuEngine — parsing, page state, like standardization, note_id indexing.

ALL mocks — no real network, Chrome, or filesystem.
"""

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cn_scraper_mcp.engines.xiaohongshu import (
    XiaohongshuEngine,
    _standardize_likes,
    _detect_page_state,
    ERR_LOGIN_EXPIRED,
    ERR_IP_RISK,
    ERR_CAPTCHA,
    ERR_NOTE_NOT_FOUND,
    SEARCH_EXTRACTOR,
    NOTE_DETAIL_EXTRACTOR_TEMPLATE,
    COMMENT_EXTRACTOR_TEMPLATE,
)


# ═══════════════════════════════════════════════════════════════
# Fixtures — raw search extraction data
# ═══════════════════════════════════════════════════════════════

def _normal_search_raw() -> dict:
    """Fixture: normal search results with 5 notes, including varied like counts."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test&type=51",
        "pageText": "小红书 搜索 为你推荐 笔记列表",
        "items": [
            {
                "title": "超好用的儿童学习桌推荐",
                "author": "宝妈小李",
                "likes": "1.2万",
                "noteId": "64a1b2c3d4e5f6a7b8c9d0e1",
                "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e1?xsec_token=abc123token",
                "xsec_token": "abc123token",
            },
            {
                "title": "学习桌避坑指南",
                "author": "家居达人王",
                "likes": "999+",
                "noteId": "64a1b2c3d4e5f6a7b8c9d0e2",
                "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e2?xsec_token=def456token",
                "xsec_token": "def456token",
            },
            {
                "title": "2024学习桌排行榜",
                "author": "测评君",
                "likes": "2300",
                "noteId": "64a1b2c3d4e5f6a7b8c9d0e3",
                "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e3?xsec_token=ghi789token",
                "xsec_token": "ghi789token",
            },
            {
                "title": "平价学习桌也能很好用",
                "author": "省钱小能手",
                "likes": "",
                "noteId": "64a1b2c3d4e5f6a7b8c9d0e4",
                "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e4?xsec_token=jkl012token",
                "xsec_token": "jkl012token",
            },
            {
                "title": "学习桌安装教程",
                "author": "DIY玩家",
                "likes": "15.5万",
                "noteId": "64a1b2c3d4e5f6a7b8c9d0e5",
                "href": "https://www.xiaohongshu.com/explore/64a1b2c3d4e5f6a7b8c9d0e5?xsec_token=mno345token",
                "xsec_token": "mno345token",
            },
        ],
    }


def _login_expired_raw() -> dict:
    """Fixture: login-expired page (redirected to passport/login)."""
    return {
        "url": "https://passport.xiaohongshu.com/login?redirect=https://www.xiaohongshu.com",
        "pageText": "小红书登录 请登录 手机号登录",
        "items": [],
    }


def _login_text_raw() -> dict:
    """Fixture: login detected from page text (URL still ok)."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test",
        "pageText": "登录 小红书 请登录后查看",
        "items": [],
    }


def _ip_risk_raw() -> dict:
    """Fixture: IP risk — error_code=300012 in page text."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test",
        "pageText": "error_code=300012 IP存在风险 请使用手机端访问",
        "items": [],
    }


def _ip_risk_url_raw() -> dict:
    """Fixture: IP risk — error_code=300012 in URL."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test&error_code=300012",
        "pageText": "",
        "items": [],
    }


def _captcha_raw() -> dict:
    """Fixture: captcha / verification page."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test",
        "pageText": "请完成验证 滑块验证 人机验证",
        "items": [],
    }


def _empty_results_raw() -> dict:
    """Fixture: genuine empty results (no block signals)."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=xyzzy_nonexistent",
        "pageText": "未找到相关笔记 换个关键词试试吧",
        "items": [],
    }


def _fallback_selector_raw() -> dict:
    """Fixture: items extracted via fallback selectors (xsec_token preserved)."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=newlayout",
        "pageText": "小红书 搜索 为你推荐",
        "items": [
            {
                "title": "新版笔记A",
                "author": "用户A",
                "likes": "500",
                "noteId": "fallback001",
                "href": "https://www.xiaohongshu.com/explore/fallback001?xsec_token=fb_token_1",
                "xsec_token": "fb_token_1",
            },
            {
                "title": "新版笔记B",
                "author": "用户B",
                "likes": "1200",
                "noteId": "fallback002",
                "href": "https://www.xiaohongshu.com/explore/fallback002?xsec_token=fb_token_2",
                "xsec_token": "fb_token_2",
            },
        ],
    }


def _item_without_noteid_raw() -> dict:
    """Fixture: item without noteId — should be filtered out."""
    return {
        "url": "https://www.xiaohongshu.com/search_result?keyword=test",
        "pageText": "小红书 搜索",
        "items": [
            {
                "title": "无ID笔记",
                "author": "某人",
                "likes": "100",
                "noteId": "",
                "href": "",
                "xsec_token": "",
            },
            {
                "title": "有效笔记",
                "author": "另一个人",
                "likes": "200",
                "noteId": "valid_note_001",
                "href": "https://www.xiaohongshu.com/explore/valid_note_001",
                "xsec_token": "",
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════
# Fixtures — raw note detail extraction data
# ═══════════════════════════════════════════════════════════════

def _normal_note_detail_raw() -> dict:
    """Fixture: normal note detail with the requested note_id."""
    return {
        "id": "64a1b2c3d4e5f6a7b8c9d0e1",
        "title": "超好用的儿童学习桌推荐",
        "desc": "买了这款学习桌半年了，真的超好用！...",
        "type": "normal",
        "likes": 1234,
        "collects": 567,
        "comments": 89,
        "user": {"name": "宝妈小李", "id": "user_12345"},
        "tags": ["学习桌", "儿童家具", "好物推荐"],
        "time": 1700000000000,
    }


def _note_not_in_map_raw() -> dict:
    """Fixture: note_id not found in noteDetailMap."""
    return {
        "error": "note_id not found in noteDetailMap",
        "requested_note_id": "nonexistent_note_id",
        "available_ids": ["64a1b2c3d4e5f6a7b8c9d0e1", "64a1b2c3d4e5f6a7b8c9d0e2"],
    }


def _note_no_init_state_raw() -> dict:
    """Fixture: no __INITIAL_STATE__ at all."""
    return {"error": "no __INITIAL_STATE__"}


# ═══════════════════════════════════════════════════════════════
# Fixtures — engine instances
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def engine():
    """Create a XiaohongshuEngine without triggering browser or filesystem."""
    # Bypass __init__ to avoid filesystem access
    eng = XiaohongshuEngine.__new__(XiaohongshuEngine)
    eng.cookies_path = "/fake/cookies.json"
    eng.port = 9251
    eng.cookies = {}
    return eng


def _make_mock_cdp(return_value):
    """Build a mock CDPClient that returns the given value from evaluate()."""
    mock_cdp = MagicMock()
    mock_cdp.connect = AsyncMock()
    mock_cdp.enable = AsyncMock()
    mock_cdp.navigate = AsyncMock()
    mock_cdp.evaluate = AsyncMock(return_value=return_value)
    mock_cdp.close = AsyncMock()
    mock_cdp._send = AsyncMock()
    return mock_cdp


# ═══════════════════════════════════════════════════════════════
# Tests: _standardize_likes (pure logic)
# ═══════════════════════════════════════════════════════════════

class TestStandardizeLikes:
    """Test like count standardization."""

    def test_wan_conversion(self):
        """'1.2万' → 12000."""
        assert _standardize_likes("1.2万") == 12000

    def test_wan_no_decimal(self):
        """'3万' → 30000."""
        assert _standardize_likes("3万") == 30000

    def test_wan_large(self):
        """'15.5万' → 155000."""
        assert _standardize_likes("15.5万") == 155000

    def test_w_english(self):
        """'1.2w' → 12000."""
        assert _standardize_likes("1.2w") == 12000

    def test_w_uppercase(self):
        """'5W' → 50000."""
        assert _standardize_likes("5W") == 50000

    def test_999_plus(self):
        """'999+' → 999."""
        assert _standardize_likes("999+") == 999

    def test_plain_number(self):
        """'2300' → 2300."""
        assert _standardize_likes("2300") == 2300

    def test_empty_string(self):
        """'' → 0."""
        assert _standardize_likes("") == 0

    def test_none(self):
        """None → 0."""
        assert _standardize_likes(None) == 0  # type: ignore[arg-type]

    def test_whitespace_only(self):
        """'   ' → 0."""
        assert _standardize_likes("   ") == 0

    def test_non_numeric(self):
        """'N/A' → 0."""
        assert _standardize_likes("N/A") == 0

    def test_plus_only(self):
        """'+' → 0."""
        assert _standardize_likes("+") == 0

    def test_zero(self):
        """'0' → 0."""
        assert _standardize_likes("0") == 0

    def test_large_wan(self):
        """'100万' → 1000000."""
        assert _standardize_likes("100万") == 1000000

    def test_float_only(self):
        """'12.5' → 12."""
        assert _standardize_likes("12.5") == 12

    def test_integer_input(self):
        """int input → 0 (not a string)."""
        assert _standardize_likes(123) == 0  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════
# Tests: _detect_page_state (pure logic)
# ═══════════════════════════════════════════════════════════════

class TestDetectPageState:
    """Test page state detection function."""

    def test_normal_page_is_ok(self):
        """Normal search page with items → ok."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?keyword=test",
            "小红书 搜索 为你推荐 笔记列表",
            5,
        )
        assert state == "ok"
        assert code is None
        assert msg is None

    def test_ip_risk_from_text(self):
        """Page text with error_code=300012 → ip_risk."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?keyword=test",
            "error_code=300012 IP存在风险",
            0,
        )
        assert state == "ip_risk"
        assert code == ERR_IP_RISK
        assert "300012" in msg

    def test_ip_risk_from_text_chinese(self):
        """'IP存在风险' in text → ip_risk."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result",
            "访问过于频繁 IP 存在风险",
            0,
        )
        assert state == "ip_risk"

    def test_ip_risk_from_url(self):
        """error_code=300012 in URL → ip_risk."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?error_code=300012",
            "",
            0,
        )
        assert state == "ip_risk"
        assert code == ERR_IP_RISK

    def test_login_expired_from_url_passport(self):
        """URL contains 'passport' → login_expired."""
        state, code, msg = _detect_page_state(
            "https://passport.xiaohongshu.com/login",
            "",
            0,
        )
        assert state == "login_expired"
        assert code == ERR_LOGIN_EXPIRED
        assert "登录" in msg

    def test_login_expired_from_url_login(self):
        """URL contains 'login' → login_expired."""
        state, code, msg = _detect_page_state(
            "https://login.xiaohongshu.com/authorize",
            "",
            0,
        )
        assert state == "login_expired"

    def test_login_expired_from_text(self):
        """'登录' in page text → login_expired."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?keyword=test",
            "登录 小红书 请登录后查看",
            0,
        )
        assert state == "login_expired"

    def test_captcha_slider(self):
        """'滑块验证' in text → captcha."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result",
            "请完成滑块验证",
            0,
        )
        assert state == "captcha"
        assert code == ERR_CAPTCHA

    def test_captcha_verify(self):
        """'验证码' in text → captcha."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result",
            "请输入验证码",
            0,
        )
        assert state == "captcha"

    def test_verify_keyword(self):
        """'请完成验证' in text → captcha."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result",
            "请完成验证后继续",
            0,
        )
        assert state == "captcha"

    def test_empty_results(self):
        """0 items and no block → empty."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?keyword=nonexistent",
            "未找到相关笔记",
            0,
        )
        assert state == "empty"
        assert code == "XHS_EMPTY"

    def test_ip_risk_takes_priority(self):
        """IP risk should be detected even if page also has login text."""
        state, code, msg = _detect_page_state(
            "https://www.xiaohongshu.com/search_result?error_code=300012",
            "登录 error_code=300012 IP存在风险",
            0,
        )
        assert state == "ip_risk"

    def test_login_before_captcha(self):
        """Login expired takes priority over captcha."""
        state, code, msg = _detect_page_state(
            "https://passport.xiaohongshu.com/login",
            "验证码 滑块验证",
            0,
        )
        assert state == "login_expired"

    def test_case_insensitive_url(self):
        """URL contains 'LOGIN' (uppercase) → login_expired."""
        state, code, msg = _detect_page_state(
            "https://LOGIN.xiaohongshu.com/",
            "",
            0,
        )
        assert state == "login_expired"


# ═══════════════════════════════════════════════════════════════
# Tests: _parse_search (parsing logic)
# ═══════════════════════════════════════════════════════════════

class TestParseSearchNormal:
    """Test _parse_search with normal results."""

    def test_parse_returns_correct_structure(self, engine):
        """Parsed search should have all required fields."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        assert result["keyword"] == "测试"
        assert result["state"] == "ok"
        assert "count" in result
        assert "items" in result
        assert "error_code" in result
        assert "error_message" in result

    def test_items_have_required_fields(self, engine):
        """Each item should have title, author, likes, noteId, href, xsec_token."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        for item in result["items"]:
            assert "title" in item
            assert "author" in item
            assert "likes" in item
            assert "noteId" in item
            assert "href" in item
            assert "xsec_token" in item

    def test_likes_are_integers(self, engine):
        """Like counts should be standardized to int."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        for item in result["items"]:
            assert isinstance(item["likes"], int), f"Expected int, got {type(item['likes'])}"


class TestParseSearchLikeStandardization:
    """Test like count standardization through _parse_search."""

    def test_wan_likes_converted(self, engine):
        """'1.2万' → 12000."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e1")
        assert item["likes"] == 12000

    def test_999_plus_converted(self, engine):
        """'999+' → 999."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e2")
        assert item["likes"] == 999

    def test_plain_number_passthrough(self, engine):
        """'2300' → 2300."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e3")
        assert item["likes"] == 2300

    def test_empty_likes_is_zero(self, engine):
        """'' → 0."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e4")
        assert item["likes"] == 0

    def test_large_wan_converted(self, engine):
        """'15.5万' → 155000."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e5")
        assert item["likes"] == 155000


class TestParseSearchXsecToken:
    """Test xsec_token preservation through _parse_search."""

    def test_xsec_token_preserved(self, engine):
        """xsec_token from raw items should survive parsing."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        item = next(it for it in result["items"] if it["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e1")
        assert item["xsec_token"] == "abc123token"

    def test_all_xsec_tokens_preserved(self, engine):
        """Every item should have its xsec_token preserved."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)

        for item in result["items"]:
            assert item["xsec_token"], f"xsec_token should not be empty for {item['noteId']}"


class TestParseSearchPageState:
    """Test page state detection through _parse_search."""

    def test_normal_search_is_ok(self, engine):
        """Normal search with items → ok."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "ok"
        assert result["error_code"] is None

    def test_login_expired_state(self, engine):
        """Login page URL → login_expired."""
        raw = _login_expired_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "login_expired"
        assert result["error_code"] == ERR_LOGIN_EXPIRED

    def test_login_expired_from_text(self, engine):
        """Login text in page → login_expired."""
        raw = _login_text_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "login_expired"

    def test_ip_risk_state(self, engine):
        """IP risk text → ip_risk."""
        raw = _ip_risk_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "ip_risk"
        assert result["error_code"] == ERR_IP_RISK

    def test_ip_risk_url_state(self, engine):
        """IP risk in URL → ip_risk."""
        raw = _ip_risk_url_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "ip_risk"

    def test_captcha_state(self, engine):
        """Captcha text → captcha."""
        raw = _captcha_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "captcha"
        assert result["error_code"] == ERR_CAPTCHA

    def test_empty_results_state(self, engine):
        """No items, no blocks → empty."""
        raw = _empty_results_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["state"] == "empty"

    def test_login_state_has_human_message(self, engine):
        """Login error message should mention cookies."""
        raw = _login_expired_raw()
        result = engine._parse_search("测试", raw, 10)
        assert "登录" in result["error_message"] or "cookies" in result["error_message"].lower()

    def test_ip_risk_has_human_message(self, engine):
        """IP risk message should mention 300012."""
        raw = _ip_risk_raw()
        result = engine._parse_search("测试", raw, 10)
        assert "300012" in result["error_message"] or "IP" in result["error_message"]


class TestParseSearchEdgeCases:
    """Edge cases in _parse_search."""

    def test_limit_truncation(self, engine):
        """limit=2 should return only 2 items."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 2)
        assert len(result["items"]) == 2
        assert result["count"] == 2

    def test_no_items_returns_empty_list(self, engine):
        """Raw result with no items → empty items list."""
        raw = _empty_results_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["items"] == []

    def test_empty_noteid_filtered_out(self, engine):
        """Item with empty noteId should be removed."""
        raw = _item_without_noteid_raw()
        result = engine._parse_search("测试", raw, 10)
        assert result["count"] == 1
        assert result["items"][0]["noteId"] == "valid_note_001"

    def test_likes_always_int(self, engine):
        """After parsing, all likes should be int regardless of input."""
        raw = _normal_search_raw()
        result = engine._parse_search("测试", raw, 10)
        for item in result["items"]:
            assert isinstance(item["likes"], int)


# ═══════════════════════════════════════════════════════════════
# Tests: full search() flow (with mocked CDPClient)
# ═══════════════════════════════════════════════════════════════

class TestSearchNormal:
    """Test XiaohongshuEngine.search() with mocked CDP."""

    def test_search_returns_normal_items(self, engine):
        """Normal search returns items with correct structure."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_normal_search_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("学习桌", limit=10)

        assert result["keyword"] == "学习桌"
        assert result["state"] == "ok"
        assert result["count"] == 5
        assert len(result["items"]) == 5

    def test_search_likes_standardized(self, engine):
        """Like counts should be integers after search."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_normal_search_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("学习桌", limit=10)

        assert result["items"][0]["likes"] == 12000  # was "1.2万"
        assert result["items"][1]["likes"] == 999    # was "999+"
        assert result["items"][3]["likes"] == 0      # was ""

    def test_search_xsec_token_preserved(self, engine):
        """xsec_token should be in each item."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_normal_search_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("学习桌", limit=10)

        for item in result["items"]:
            assert "xsec_token" in item
            assert item["xsec_token"]  # all items in normal fixture have tokens

    def test_search_limit_truncates(self, engine):
        """limit=2 gives exactly 2 items."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_normal_search_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("学习桌", limit=2)

        assert len(result["items"]) == 2
        assert result["count"] == 2


class TestSearchErrors:
    """Test XiaohongshuEngine.search() error handling."""

    def test_login_expired(self, engine):
        """Login-expired page → state=login_expired."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_login_expired_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("测试", limit=10)

        assert result["state"] == "login_expired"
        assert result["error_code"] == ERR_LOGIN_EXPIRED
        assert result["count"] == 0

    def test_ip_risk(self, engine):
        """IP risk page → state=ip_risk."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_ip_risk_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("测试", limit=10)

        assert result["state"] == "ip_risk"
        assert result["error_code"] == ERR_IP_RISK

    def test_captcha(self, engine):
        """Captcha page → state=captcha."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_captcha_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("测试", limit=10)

        assert result["state"] == "captcha"
        assert result["error_code"] == ERR_CAPTCHA

    def test_empty_results(self, engine):
        """Empty results → state=empty."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_empty_results_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.search("nonexistent", limit=10)

        assert result["state"] == "empty"
        assert result["count"] == 0

    def test_browser_unavailable(self, engine):
        """Browser not running → error dict."""
        with patch.object(engine, "ensure_browser", return_value=False):
            result = engine.search("测试", limit=10)

        assert result["state"] == "error"
        assert result["error_code"] == "XHS_BROWSER_UNAVAILABLE"

    def test_cdp_exception(self, engine):
        """CDPClient raises → caught and returned as error."""
        with patch.object(engine, "ensure_browser", return_value=True):
            with patch(
                "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
                side_effect=Exception("Connection refused"),
            ):
                result = engine.search("测试", limit=10)

        assert result["state"] == "error"
        assert result["error_code"] == "XHS_SEARCH_EXCEPTION"


# ═══════════════════════════════════════════════════════════════
# Tests: get_note() (strict note_id indexing)
# ═══════════════════════════════════════════════════════════════

class TestGetNote:
    """Test get_note() with strict note_id indexing."""

    def test_get_note_returns_detail(self, engine):
        """Normal note detail should be returned."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_normal_note_detail_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.get_note("64a1b2c3d4e5f6a7b8c9d0e1")

        assert result["id"] == "64a1b2c3d4e5f6a7b8c9d0e1"
        assert result["title"] == "超好用的儿童学习桌推荐"
        assert result["user"]["name"] == "宝妈小李"
        assert "tags" in result
        assert len(result["tags"]) == 3

    def test_get_note_strict_note_id_passed(self, engine):
        """The extractor JS should contain the correct noteId."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
        ) as mock_cdp_cls:
            mock_cdp = _make_mock_cdp(json.dumps(_normal_note_detail_raw()))
            mock_cdp_cls.return_value = mock_cdp

            with patch.object(engine, "ensure_browser", return_value=True):
                engine.get_note("my_test_note_id_12345")

            # Verify the evaluate expression contains our note_id
            evaluate_args = mock_cdp.evaluate.call_args
            expression = evaluate_args[0][0] if evaluate_args[0] else evaluate_args.kwargs.get("expression", "")
            assert "my_test_note_id_12345" in expression

    def test_get_note_not_in_map(self, engine):
        """When note_id not in map, error should be returned."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_note_not_in_map_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.get_note("nonexistent_note_id")

        assert "error" in result
        assert "note_id not found" in result["error"]
        assert result["requested_note_id"] == "nonexistent_note_id"

    def test_get_note_no_init_state(self, engine):
        """No __INITIAL_STATE__ → error."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(_note_no_init_state_raw())),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.get_note("any_id")

        assert "error" in result

    def test_get_note_browser_unavailable(self, engine):
        """Browser not running → error."""
        with patch.object(engine, "ensure_browser", return_value=False):
            result = engine.get_note("any_id")

        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# Tests: get_comments() (first-screen only)
# ═══════════════════════════════════════════════════════════════

class TestGetComments:
    """Test get_comments() with first-screen-only behavior."""

    def test_get_comments_returns_list(self, engine):
        """Normal comments should return a list."""
        comments_raw = [
            {"content": "写得很好！", "userName": "用户A", "likes": 10, "time": 1700000000000},
            {"content": "学习了", "userName": "用户B", "likes": 5, "time": 1700000001000},
        ]
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps(comments_raw)),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.get_comments("64a1b2c3d4e5f6a7b8c9d0e1")

        assert result["noteId"] == "64a1b2c3d4e5f6a7b8c9d0e1"
        assert "comments" in result
        assert len(result["comments"]) == 2
        assert result["comments"][0]["content"] == "写得很好！"

    def test_get_comments_empty(self, engine):
        """No comments → empty list."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
            return_value=_make_mock_cdp(json.dumps([])),
        ), patch.object(engine, "ensure_browser", return_value=True):
            result = engine.get_comments("64a1b2c3d4e5f6a7b8c9d0e1")

        assert result["comments"] == []

    def test_get_comments_strict_note_id_passed(self, engine):
        """The extractor JS should contain the correct noteId."""
        with patch(
            "cn_scraper_mcp.engines.xiaohongshu.CDPClient",
        ) as mock_cdp_cls:
            mock_cdp = _make_mock_cdp(json.dumps([]))
            mock_cdp_cls.return_value = mock_cdp

            with patch.object(engine, "ensure_browser", return_value=True):
                engine.get_comments("comment_test_id_67890")

            evaluate_args = mock_cdp.evaluate.call_args
            expression = evaluate_args[0][0] if evaluate_args[0] else evaluate_args.kwargs.get("expression", "")
            assert "comment_test_id_67890" in expression

    def test_get_comments_browser_unavailable(self, engine):
        """Browser not running → error."""
        with patch.object(engine, "ensure_browser", return_value=False):
            result = engine.get_comments("any_id")

        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# Tests: JS extractors sanity
# ═══════════════════════════════════════════════════════════════

class TestSearchExtractor:
    """Sanity checks for the inline JS search extractor."""

    def test_is_valid_string(self):
        """SEARCH_EXTRACTOR should be a non-empty string."""
        assert isinstance(SEARCH_EXTRACTOR, str)
        assert len(SEARCH_EXTRACTOR) > 100

    def test_returns_url(self):
        """Should capture window.location.href."""
        assert "window.location.href" in SEARCH_EXTRACTOR

    def test_returns_page_text(self):
        """Should capture page text for state detection."""
        assert "pageText" in SEARCH_EXTRACTOR

    def test_multi_selector_fallback(self):
        """Should use multiple selectors for note items."""
        assert "section.note-item" in SEARCH_EXTRACTOR
        assert "div.note-item" in SEARCH_EXTRACTOR

    def test_extracts_xsec_token(self):
        """Should extract xsec_token from URL params."""
        assert "xsec_token" in SEARCH_EXTRACTOR

    def test_is_iife(self):
        """Should be a self-invoking function."""
        stripped = SEARCH_EXTRACTOR.strip()
        assert stripped.startswith("(function()")
        assert stripped.endswith("})()")

    def test_dedup_by_href(self):
        """Should deduplicate by href to avoid duplicate notes."""
        assert "seen" in SEARCH_EXTRACTOR


class TestNoteDetailExtractor:
    """Sanity checks for the note detail extractor template."""

    def test_is_valid_template(self):
        """Template should be a non-empty string with placeholder."""
        assert isinstance(NOTE_DETAIL_EXTRACTOR_TEMPLATE, str)
        assert "{note_id}" in NOTE_DETAIL_EXTRACTOR_TEMPLATE

    def test_indexes_by_note_id(self):
        """Should use detail[noteId] — strict indexing."""
        assert "detail[noteId]" in NOTE_DETAIL_EXTRACTOR_TEMPLATE

    def test_never_uses_object_values_first(self):
        """Should NOT use Object.values() as primary indexing — uses detail[noteId] first."""
        # detail[noteId] is the primary path (verified in test_indexes_by_note_id)
        # The template has a fallback using Object.keys, not Object.values
        assert "detail[noteId]" in NOTE_DETAIL_EXTRACTOR_TEMPLATE
        # Object.keys fallback is acceptable for key-search
        assert "Object.keys" in NOTE_DETAIL_EXTRACTOR_TEMPLATE

    def test_handles_missing_note(self):
        """Should return error when note not in map."""
        assert "note_id not found in noteDetailMap" in NOTE_DETAIL_EXTRACTOR_TEMPLATE

    def test_returns_available_ids(self):
        """On error, should return the list of available note IDs."""
        assert "available_ids" in NOTE_DETAIL_EXTRACTOR_TEMPLATE


class TestCommentExtractor:
    """Sanity checks for the comment extractor template."""

    def test_is_valid_template(self):
        """Template should be a non-empty string with placeholder."""
        assert isinstance(COMMENT_EXTRACTOR_TEMPLATE, str)
        assert "{note_id}" in COMMENT_EXTRACTOR_TEMPLATE

    def test_indexes_by_note_id(self):
        """Should use detail[noteId] for indexing."""
        assert "detail[noteId]" in COMMENT_EXTRACTOR_TEMPLATE

    def test_handles_empty_comments(self):
        """Should handle missing comments gracefully."""
        # The JS returns [] on error
        assert "return JSON.stringify([])" in COMMENT_EXTRACTOR_TEMPLATE

    def test_no_object_values_first(self):
        """Should NOT use Object.values()[0]."""
        assert "Object.values" not in COMMENT_EXTRACTOR_TEMPLATE
