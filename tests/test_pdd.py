"""Unit tests for PDDEngine — parsing, rate-limit detection, auth errors, sold-out.

ALL mocks — no real network, Chrome, or filesystem.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cn_scraper_mcp.engines.pdd import (
    DETAIL_EXTRACT_JS,
    PDD_PORT,
    SEARCH_EXTRACT_JS,
    PDDAuthError,
    PDDEngine,
    PDDParseError,
    PDDRateLimitError,
    PDDSoldOutError,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures — raw CDP extraction data (what SEARCH_EXTRACT_JS returns)
# ═══════════════════════════════════════════════════════════════


def _normal_search_raw() -> dict:
    """Fixture: normal search results with 4 products."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test",
        "title": "test - 拼多多",
        "ogTitle": "",
        "pageText": "拼多多 搜索 test 为你推荐 综合排序 销量优先",
        "rateLimited": False,
        "itemCount": 4,
        "items": [
            {
                "goodsId": "1234567890",
                "name": "超好用的儿童学习桌 可升降 实木",
                "price": 299.0,
                "sold": 15000,
            },
            {
                "goodsId": "1234567891",
                "name": "简约现代学习桌 家用书桌",
                "price": 199.0,
                "sold": 8900,
            },
            {
                "goodsId": "1234567892",
                "name": "护眼学习桌 进口实木 环保漆",
                "price": 599.0,
                "sold": 3200,
            },
            {
                "goodsId": "1234567893",
                "name": "折叠学习桌 便携 学生桌",
                "price": 89.0,
                "sold": 55000,
            },
        ],
    }


def _duplicate_items_raw() -> dict:
    """Fixture: search results with duplicate goodsIds — should be deduped."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test",
        "title": "",
        "ogTitle": "",
        "pageText": "",
        "rateLimited": False,
        "itemCount": 3,
        "items": [
            {"goodsId": "dup_001", "name": "商品A", "price": 10.0, "sold": 100},
            {"goodsId": "dup_001", "name": "商品A 重复", "price": 10.0, "sold": 100},
            {"goodsId": "dup_002", "name": "商品B", "price": 20.0, "sold": 200},
        ],
    }


def _rate_limited_raw() -> dict:
    """Fixture: PDD rate-limit page — '系统繁忙'."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test",
        "title": "拼多多",
        "ogTitle": "",
        "pageText": "系统繁忙，请稍后再试 网络异常",
        "rateLimited": True,
        "itemCount": 0,
        "items": [],
    }


def _rate_limited_text_raw() -> dict:
    """Fixture: rate-limit detected from pageText (rateLimited flag may be false).

    Real PDD sometimes returns rateLimited=False in JS but has '系统繁忙' in text.
    """
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test2",
        "title": "拼多多",
        "ogTitle": "",
        "pageText": "网络异常 系统繁忙 请稍后再试",
        "rateLimited": False,  # JS may miss it
        "itemCount": 0,
        "items": [],
    }


def _login_gated_raw() -> dict:
    """Fixture: login-gated page — og:title='拼多多商城' with no product data."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test",
        "title": "拼多多商城",
        "ogTitle": "拼多多商城",
        "pageText": "拼多多 新电商开创者 手机拼多多 更懂你的购物",
        "rateLimited": False,
        "itemCount": 0,
        "items": [],
    }


def _empty_results_raw() -> dict:
    """Fixture: genuine empty search results."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=nonexistent_xyz",
        "title": "nonexistent_xyz - 拼多多",
        "ogTitle": "",
        "pageText": "没有找到相关商品 试试其他关键词",
        "rateLimited": False,
        "itemCount": 0,
        "items": [],
    }


def _no_goods_id_raw() -> dict:
    """Fixture: items without goodsId — should still work with synthetic IDs."""
    return {
        "url": "https://mobile.yangkeduo.com/search_result.html?search_key=test",
        "title": "",
        "ogTitle": "",
        "pageText": "",
        "rateLimited": False,
        "itemCount": 2,
        "items": [
            {"goodsId": "", "name": "无名商品A", "price": 50.0, "sold": 100},
            {"goodsId": "", "name": "无名商品B", "price": 75.0, "sold": 200},
        ],
    }


def _price_string_raw() -> dict:
    """Fixture: price returned as string instead of float."""
    return {
        "url": "",
        "title": "",
        "ogTitle": "",
        "pageText": "",
        "rateLimited": False,
        "itemCount": 1,
        "items": [
            {"goodsId": "str_001", "name": "字符串价格商品", "price": "199.00", "sold": 0},
        ],
    }


# ═══════════════════════════════════════════════════════════════
# Fixtures — product detail (what DETAIL_EXTRACT_JS returns)
# ═══════════════════════════════════════════════════════════════


def _detail_normal_raw() -> dict:
    """Fixture: normal product detail page."""
    return {
        "url": "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890",
        "title": "超好用的儿童学习桌 可升降 实木 - 拼多多",
        "ogTitle": "",
        "loginGated": False,
        "soldOut": False,
        "name": "超好用的儿童学习桌 可升降 实木",
        "price": 299.0,
        "origPrice": 599.0,
        "sales": "1.5万",
        "specs": ["颜色: 原木色", "尺码: 120cm"],
        "pageText": "超好用的儿童学习桌 可升降 实木 ¥299 ¥599 1.5万件 颜色: 原木色 尺码: 120cm",
    }


def _detail_sold_out_raw() -> dict:
    """Fixture: sold-out product detail."""
    return {
        "url": "https://mobile.yangkeduo.com/goods2.html?goods_id=9999999999",
        "title": "已售罄商品 - 拼多多",
        "ogTitle": "",
        "loginGated": False,
        "soldOut": True,
        "name": "已售罄商品",
        "price": 99.0,
        "origPrice": 199.0,
        "sales": "0",
        "specs": [],
        "pageText": "已售罄商品 ¥99 商品已售罄 已卖光",
    }


def _detail_login_gated_raw() -> dict:
    """Fixture: login-gated product detail."""
    return {
        "url": "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890",
        "title": "拼多多商城",
        "ogTitle": "拼多多商城",
        "loginGated": True,
        "soldOut": False,
        "name": "",
        "price": None,
        "origPrice": None,
        "sales": "",
        "specs": [],
        "pageText": "拼多多 新电商开创者",
    }


def _detail_not_found_raw() -> dict:
    """Fixture: product not found / taken down."""
    return {
        "url": "https://mobile.yangkeduo.com/goods2.html?goods_id=0000000000",
        "title": "拼多多",
        "ogTitle": "",
        "loginGated": False,
        "soldOut": False,
        "name": "",
        "price": None,
        "origPrice": None,
        "sales": "",
        "specs": [],
        "pageText": "商品已下架 商品不存在",
    }


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


def _make_mock_cdp(return_value):
    """Build a mock CDPClient that returns the given value from evaluate()."""
    mock_cdp = MagicMock()
    mock_cdp.connect = AsyncMock()
    mock_cdp.enable = AsyncMock()
    mock_cdp.navigate = AsyncMock()
    mock_cdp.evaluate = AsyncMock(return_value=return_value)
    mock_cdp.close = AsyncMock()
    mock_cdp._send = AsyncMock()  # for _inject_cookies
    return mock_cdp


def _make_engine_with_cookies():
    """Create a PDDEngine with mock cookies, bypassing filesystem."""
    eng = PDDEngine.__new__(PDDEngine)
    eng.cookies_path = "/fake/pdd.json"
    eng.port = PDD_PORT
    eng._cdp = None
    eng._searched = False
    eng._cookies = {
        "PDDAccessToken": "fake_token_abc123",
        "pdd_user_id": "1234567890",
        "pdd_user_name": "test_user",
    }
    return eng


def _make_engine_no_cookies():
    """Create a PDDEngine WITHOUT cookies."""
    eng = PDDEngine.__new__(PDDEngine)
    eng.cookies_path = "/fake/pdd.json"
    eng.port = PDD_PORT
    eng._cdp = None
    eng._searched = False
    eng._cookies = {}
    return eng


# ═══════════════════════════════════════════════════════════════
# Tests: has_valid_cookies
# ═══════════════════════════════════════════════════════════════


class TestCookieValidation:
    """Test cookie presence checks."""

    def test_valid_cookies_returns_true(self):
        """Engine with both PDDAccessToken and pdd_user_id has valid cookies."""
        eng = _make_engine_with_cookies()
        assert eng.has_valid_cookies is True

    def test_missing_access_token_returns_false(self):
        """Missing PDDAccessToken → invalid."""
        eng = _make_engine_no_cookies()
        eng._cookies = {"pdd_user_id": "123"}
        assert eng.has_valid_cookies is False

    def test_missing_user_id_returns_false(self):
        """Missing pdd_user_id → invalid."""
        eng = _make_engine_no_cookies()
        eng._cookies = {"PDDAccessToken": "xxx"}
        assert eng.has_valid_cookies is False

    def test_empty_cookies_returns_false(self):
        """No cookies at all → invalid."""
        eng = _make_engine_no_cookies()
        assert eng.has_valid_cookies is False


# ═══════════════════════════════════════════════════════════════
# Tests: _process_search_result (pure logic, no mocking)
# ═══════════════════════════════════════════════════════════════


class TestProcessSearchResultNormal:
    """Test _process_search_result with valid data."""

    def test_normal_4_items(self):
        """4 products → all returned with correct structure."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        assert result["state"] == "ok"
        assert result["count"] == 4
        assert result["keyword"] == "test"
        assert len(result["items"]) == 4

    def test_item_has_required_fields(self):
        """Each item should have goodsId, name, price, sold, url."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        for item in result["items"]:
            assert "goodsId" in item
            assert "name" in item
            assert "price" in item
            assert "sold" in item
            assert "url" in item

    def test_item_url_format(self):
        """URL should be goods2.html?goods_id={id}."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        item = result["items"][0]
        assert item["goodsId"] == "1234567890"
        assert item["url"] == "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890"

    def test_price_is_float(self):
        """Price should be a float."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        item = result["items"][0]
        assert item["price"] == 299.0
        assert isinstance(item["price"], float)

    def test_sold_is_int(self):
        """Sold count should be an int."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        item = result["items"][0]
        assert item["sold"] == 15000
        assert isinstance(item["sold"], int)

    def test_limit_truncates(self):
        """limit=2 should return only 2 items."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 2)

        assert len(result["items"]) == 2
        assert result["count"] == 4  # count reflects total, not truncated


class TestProcessSearchResultDedup:
    """Test deduplication by goodsId."""

    def test_duplicate_skipped(self):
        """Duplicate goodsId should be removed (first one kept)."""
        eng = _make_engine_with_cookies()
        raw = _duplicate_items_raw()
        result = eng._process_search_result(raw, "test", 10)

        assert result["count"] == 2  # 3 raw, 1 dup → 2
        ids = [it["goodsId"] for it in result["items"]]
        assert ids == ["dup_001", "dup_002"]

    def test_first_occurrence_kept(self):
        """When duplicate goodsId, first occurrence name is kept."""
        eng = _make_engine_with_cookies()
        raw = _duplicate_items_raw()
        result = eng._process_search_result(raw, "test", 10)

        dup_item = next(it for it in result["items"] if it["goodsId"] == "dup_001")
        assert dup_item["name"] == "商品A"

    def test_no_dup_in_normal_results(self):
        """Normal results should have all unique IDs."""
        eng = _make_engine_with_cookies()
        raw = _normal_search_raw()
        result = eng._process_search_result(raw, "test", 10)

        ids = [it["goodsId"] for it in result["items"]]
        assert len(ids) == len(set(ids))


class TestProcessSearchResultEmptyGoodsId:
    """Test items with missing goodsId."""

    def test_no_goods_id_generates_synthetic(self):
        """Items without goodsId should get synthetic IDs and empty url."""
        eng = _make_engine_with_cookies()
        raw = _no_goods_id_raw()
        result = eng._process_search_result(raw, "test", 10)

        assert result["count"] == 2
        for item in result["items"]:
            assert item["goodsId"] == ""  # synthetic IDs stripped in output
            assert item["url"] == ""
            assert item["name"] != ""

    def test_no_goods_id_no_name_skipped(self):
        """Items with no goodsId AND no name should be skipped."""
        eng = _make_engine_with_cookies()
        raw = {
            "url": "", "title": "", "ogTitle": "",
            "pageText": "", "rateLimited": False,
            "itemCount": 1,
            "items": [{"goodsId": "", "name": "", "price": None, "sold": 0}],
        }
        result = eng._process_search_result(raw, "test", 10)
        assert result["count"] == 0  # empty name + no goodsId → skipped


class TestProcessSearchResultPriceParsing:
    """Test price string → float conversion."""

    def test_string_price_converted(self):
        """Price returned as string '199.00' → float 199.0."""
        eng = _make_engine_with_cookies()
        raw = _price_string_raw()
        result = eng._process_search_result(raw, "test", 10)

        assert result["items"][0]["price"] == 199.0
        assert isinstance(result["items"][0]["price"], float)

    def test_none_price_stays_none(self):
        """None price stays None."""
        eng = _make_engine_with_cookies()
        raw = {
            "url": "", "title": "", "ogTitle": "",
            "pageText": "", "rateLimited": False,
            "itemCount": 1,
            "items": [{"goodsId": "np_001", "name": "无价格商品", "price": None, "sold": 0}],
        }
        result = eng._process_search_result(raw, "test", 10)

        assert result["items"][0]["price"] is None


# ═══════════════════════════════════════════════════════════════
# Tests: Rate limit detection
# ═══════════════════════════════════════════════════════════════


class TestRateLimit:
    """Test '系统繁忙' rate-limit detection."""

    def test_rate_limited_flag_raises(self):
        """rateLimited=True should raise PDDRateLimitError."""
        eng = _make_engine_with_cookies()
        raw = _rate_limited_raw()

        with pytest.raises(PDDRateLimitError) as excinfo:
            eng._process_search_result(raw, "test", 10)

        assert "系统繁忙" in str(excinfo.value)

    def test_rate_limited_in_text_raises(self):
        """'系统繁忙' in pageText should raise PDDRateLimitError."""
        eng = _make_engine_with_cookies()
        raw = _rate_limited_text_raw()

        with pytest.raises(PDDRateLimitError) as excinfo:
            eng._process_search_result(raw, "test", 10)

        assert "系统繁忙" in str(excinfo.value)

    def test_error_message_mentions_single_search(self):
        """Rate-limit error should explain the single-search limitation."""
        eng = _make_engine_with_cookies()

        with pytest.raises(PDDRateLimitError) as excinfo:
            eng._process_search_result(_rate_limited_raw(), "test", 10)

        assert "单次搜索" in str(excinfo.value) or "一次搜索" in str(excinfo.value)


# ═══════════════════════════════════════════════════════════════
# Tests: Auth / login-gated detection
# ═══════════════════════════════════════════════════════════════


class TestAuthErrors:
    """Test login-gated / auth error detection."""

    def test_login_gated_raises(self):
        """og:title='拼多多商城' with no products → PDDAuthError."""
        eng = _make_engine_with_cookies()
        raw = _login_gated_raw()

        with pytest.raises(PDDAuthError) as excinfo:
            eng._process_search_result(raw, "test", 10)

        assert "过期" in str(excinfo.value) or "登录" in str(excinfo.value)

    def test_login_gated_hint_mentions_token(self):
        """Auth error should mention PDDAccessToken expiry."""
        eng = _make_engine_with_cookies()

        with pytest.raises(PDDAuthError) as excinfo:
            eng._process_search_result(_login_gated_raw(), "test", 10)

        assert "PDDAccessToken" in str(excinfo.value) or "1 小时" in str(excinfo.value) or "cookie" in str(excinfo.value).lower()


# ═══════════════════════════════════════════════════════════════
# Tests: Empty results
# ═══════════════════════════════════════════════════════════════


class TestEmptyResults:
    """Test genuine empty results."""

    def test_empty_returns_state_empty(self):
        """Genuine empty results should return state='empty'."""
        eng = _make_engine_with_cookies()
        raw = _empty_results_raw()
        result = eng._process_search_result(raw, "nonexistent_xyz", 10)

        assert result["state"] == "empty"
        assert result["count"] == 0
        assert result["items"] == []
        assert result["keyword"] == "nonexistent_xyz"


# ═══════════════════════════════════════════════════════════════
# Tests: Single-search enforcement
# ═══════════════════════════════════════════════════════════════


class TestSingleSearchLimit:
    """Test that PDDEngine enforces the one-search-per-instance limit."""

    def test_second_search_raises(self):
        """Calling search() twice should raise PDDRateLimitError on second call."""
        eng = _make_engine_with_cookies()
        eng._searched = True  # Simulate already-searched state

        with pytest.raises(PDDRateLimitError) as excinfo:
            eng.search("test")

        assert "一次" in str(excinfo.value) or "ONE" in str(excinfo.value)

    def test_searched_flag_set_after_search(self):
        """After a successful search, _searched should be True."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_normal_search_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            eng.search("test", limit=2)

        assert eng._searched is True

    def test_searched_flag_set_even_on_error(self):
        """Even if search fails (CDP error), _searched should be True."""
        eng = _make_engine_with_cookies()

        with patch.object(eng, "ensure_chrome", return_value=True):
            with patch(
                "cn_scraper_mcp.engines.pdd.CDPClient",
                side_effect=Exception("Simulated error"),
            ):
                result = eng.search("test")

        assert eng._searched is True
        assert "error" in result


# ═══════════════════════════════════════════════════════════════
# Tests: search() full flow (mocked CDP)
# ═══════════════════════════════════════════════════════════════


class TestSearchFullFlow:
    """Test search() with mocked CDPClient."""

    def test_search_returns_normal_results(self):
        """Full search flow returns correct results."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_normal_search_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.search("test", limit=10)

        assert result["keyword"] == "test"
        assert result["state"] == "ok"
        assert result["count"] == 4
        assert len(result["items"]) == 4

    def test_search_rate_limited_raises(self):
        """Rate-limit page should raise PDDRateLimitError."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_rate_limited_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            with pytest.raises(PDDRateLimitError):
                eng.search("test")

    def test_search_login_gated_raises(self):
        """Login-gated page should raise PDDAuthError."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_login_gated_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            with pytest.raises(PDDAuthError):
                eng.search("test")

    def test_search_no_cookies_returns_error(self):
        """Missing cookies should return error dict."""
        eng = _make_engine_no_cookies()

        with patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.search("test")

        assert "error" in result
        assert "cookie" in result["error"].lower() or "PDDAccessToken" in result.get("hint", "")

    def test_search_chrome_not_running_returns_error(self):
        """If ensure_chrome fails, return error dict."""
        eng = _make_engine_with_cookies()

        with patch.object(eng, "ensure_chrome", return_value=False):
            result = eng.search("test")

        assert "error" in result

    def test_search_cdp_exception_returns_error(self):
        """CDP exception should be caught and returned as error dict."""
        eng = _make_engine_with_cookies()

        with patch.object(eng, "ensure_chrome", return_value=True):
            with patch(
                "cn_scraper_mcp.engines.pdd.CDPClient",
                side_effect=Exception("Connection refused"),
            ):
                result = eng.search("test")

        assert "error" in result
        assert "拼多多搜索异常" in result["error"]

    def test_search_empty_genuine(self):
        """Empty results (genuine) should return empty state."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_empty_results_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.search("nonexistent", limit=10)

        assert result["state"] == "empty"
        assert result["count"] == 0
        assert result["items"] == []


# ═══════════════════════════════════════════════════════════════
# Tests: product_detail() (mocked CDP)
# ═══════════════════════════════════════════════════════════════


class TestProductDetail:
    """Test product_detail() with mocked CDPClient."""

    def test_detail_normal(self):
        """Normal product detail returns correct data."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_normal_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.product_detail("1234567890")

        assert result["goodsId"] == "1234567890"
        assert result["name"] == "超好用的儿童学习桌 可升降 实木"
        assert result["price"] == 299.0
        assert result["origPrice"] == 599.0
        assert result["sales"] == "1.5万"
        assert result["soldOut"] is False
        assert result["state"] == "ok"
        assert len(result["specs"]) == 2

    def test_detail_sold_out(self):
        """Sold-out product should be detected."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_sold_out_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.product_detail("9999999999")

        assert result["soldOut"] is True
        assert result["state"] == "sold_out"

    def test_detail_login_gated_raises(self):
        """Login-gated detail page should raise PDDAuthError."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_login_gated_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            with pytest.raises(PDDAuthError) as excinfo:
                eng.product_detail("1234567890")

        assert "过期" in str(excinfo.value) or "登录" in str(excinfo.value)

    def test_detail_not_found(self):
        """Taken-down product → not_found state."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_not_found_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.product_detail("0000000000")

        assert result["state"] == "not_found"
        assert result["name"] is None
        assert result["price"] is None

    def test_detail_url_parsing(self):
        """Full URL with goods_id parameter should be parsed correctly."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_normal_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            result = eng.product_detail(
                "https://mobile.yangkeduo.com/goods2.html?goods_id=1234567890"
            )

        assert result["goodsId"] == "1234567890"

    def test_detail_cdp_exception_returns_error(self):
        """CDP exception should be caught."""
        eng = _make_engine_with_cookies()

        with patch.object(eng, "ensure_chrome", return_value=True):
            with patch(
                "cn_scraper_mcp.engines.pdd.CDPClient",
                side_effect=Exception("Simulated CDP error"),
            ):
                result = eng.product_detail("123")

        assert "error" in result

    def test_detail_chrome_not_running_returns_error(self):
        """If Chrome isn't running, return error."""
        eng = _make_engine_with_cookies()

        with patch.object(eng, "ensure_chrome", return_value=False):
            result = eng.product_detail("123")

        assert "error" in result

    def test_detail_does_not_affect_search_limit(self):
        """Product detail should NOT mark engine as searched."""
        eng = _make_engine_with_cookies()

        with patch(
            "cn_scraper_mcp.engines.pdd.CDPClient",
            return_value=_make_mock_cdp(_detail_normal_raw()),
        ), patch.object(eng, "ensure_chrome", return_value=True):
            eng.product_detail("1234567890")

        # _searched should still be False after product_detail
        assert eng._searched is False


# ═══════════════════════════════════════════════════════════════
# Tests: JS extractor sanity
# ═══════════════════════════════════════════════════════════════


class TestExtractJS:
    """Sanity checks for the inline JS extractor strings."""

    def test_search_extract_js_is_non_empty(self):
        """SEARCH_EXTRACT_JS should be a non-empty string."""
        assert isinstance(SEARCH_EXTRACT_JS, str)
        assert len(SEARCH_EXTRACT_JS) > 100

    def test_search_extract_js_is_iife(self):
        """SEARCH_EXTRACT_JS should be an IIFE."""
        stripped = SEARCH_EXTRACT_JS.strip()
        assert stripped.startswith("(function()")
        assert stripped.endswith("})()")

    def test_search_extract_js_detects_rate_limit(self):
        """SEARCH_EXTRACT_JS should check for '系统繁忙'."""
        assert "系统繁忙" in SEARCH_EXTRACT_JS

    def test_search_extract_js_checks_og_title(self):
        """SEARCH_EXTRACT_JS should check og:title."""
        assert "og:title" in SEARCH_EXTRACT_JS

    def test_search_extract_js_extracts_prices(self):
        """SEARCH_EXTRACT_JS should extract ¥ prices."""
        assert "[¥￥]" in SEARCH_EXTRACT_JS

    def test_detail_extract_js_detects_sold_out(self):
        """DETAIL_EXTRACT_JS should check for sold-out signals."""
        assert "商品已售罄" in DETAIL_EXTRACT_JS
        assert "已卖光" in DETAIL_EXTRACT_JS

    def test_detail_extract_js_detects_login_gated(self):
        """DETAIL_EXTRACT_JS should detect login-gated pages."""
        assert "og:title" in DETAIL_EXTRACT_JS
        assert "拼多多商城" in DETAIL_EXTRACT_JS

    def test_detail_extract_js_is_non_empty(self):
        """DETAIL_EXTRACT_JS should be a non-empty string."""
        assert isinstance(DETAIL_EXTRACT_JS, str)
        assert len(DETAIL_EXTRACT_JS) > 100


# ═══════════════════════════════════════════════════════════════
# Tests: Cookie file handling (constructor)
# ═══════════════════════════════════════════════════════════════


class TestConstructor:
    """Test PDDEngine constructor behavior."""

    def test_default_path(self):
        """Default cookies_path should be ~/.cn-scraper-cookies/pdd.json."""
        eng = PDDEngine.__new__(PDDEngine)
        eng.cookies_path = str(__import__('pathlib').Path.home() / ".cn-scraper-cookies" / "pdd.json")
        assert "pdd.json" in eng.cookies_path

    def test_port_default(self):
        """Default port should be PDD_PORT (9255)."""
        _eng = PDDEngine.__new__(PDDEngine)  # noqa: F841
        assert PDD_PORT == 9255


# ═══════════════════════════════════════════════════════════════
# Tests: Exceptions
# ═══════════════════════════════════════════════════════════════


class TestExceptions:
    """Test that all PDD exceptions can be instantiated and stringified."""

    def test_rate_limit_error(self):
        e = PDDRateLimitError("系统繁忙")
        assert "系统繁忙" in str(e)
        assert isinstance(e, Exception)

    def test_auth_error(self):
        e = PDDAuthError("Token 过期")
        assert "Token 过期" in str(e)
        assert isinstance(e, Exception)

    def test_parse_error(self):
        e = PDDParseError("解析失败")
        assert "解析失败" in str(e)
        assert isinstance(e, Exception)

    def test_sold_out_error(self):
        e = PDDSoldOutError("商品已售罄")
        assert "商品已售罄" in str(e)
        assert isinstance(e, Exception)
