"""Unit tests for TaobaoEngine.search() response parsing.

ALL mocks — no real network, filesystem, or Chrome.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from cn_scraper_mcp.engines.taobao import TaobaoAuthError, TaobaoAPIError, TaobaoEngine


# ── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """Create a TaobaoEngine that bypasses __init__ (no cookie file needed)."""
    eng = TaobaoEngine.__new__(TaobaoEngine)
    eng.cookies = {"_m_h5_tk": "abc123_test_token"}
    eng.session = MagicMock()
    return eng


def _normal_search_ret():
    """Return a realistic MTOP response for a successful search."""
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "totalResults": 428,
            "itemsArray": [
                {
                    "item_id": "789012345678",
                    "title": "华为Mate70 Pro 5G智能手机 12+256G 曜金黑",
                    "price": "6999.00",
                    "priceShowWithIcon": {
                        "price": "¥6999.00",
                        "originPrice": "¥8999.00",
                    },
                    "realSales": "2.3万+",
                    "shopInfo": {
                        "title": "华为官方旗舰店",
                        "nick": "huawei_official",
                    },
                },
                {
                    "item_id": "789012345679",
                    "title": "华为Mate70 RS 非凡大师 16+512G",
                    "price": "12999.00",
                    "priceShowWithIcon": {
                        "price": "¥12999.00",
                        "originPrice": "¥15999.00",
                    },
                    "realSales": "1.8万+",
                    "shopInfo": {"title": "华为商城自营", "nick": "vmall"},
                },
                {
                    "item_id": "789012345680",
                    "title": "华为Mate70 Pro+ 16+512G 幻影紫",
                    "price": "8999.00",
                    "priceShowWithIcon": {
                        "price": "¥8999.00",
                        "originPrice": "¥10999.00",
                    },
                    "realSales": "8900+",
                    "shopInfo": None,
                },
            ],
        },
    }


def _empty_itemsarray_ret():
    """Response where itemsArray is missing/None."""
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {"totalResults": 0},
    }


def _empty_itemsarray_zero_items_ret():
    """Response where itemsArray is present but empty."""
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {"totalResults": 0, "itemsArray": []},
    }


def _fail_token_ret():
    """Response with FAIL_SYS_TOKEN error."""
    return {
        "ret": ["FAIL_SYS_TOKEN::session expired"],
        "data": {"totalResults": 0, "itemsArray": []},
    }


def _session_expired_ret():
    """Response with SESSION_EXPIRED error."""
    return {
        "ret": ["SESSION_EXPIRED::请重新登录"],
        "data": None,
    }


def _api_error_ret():
    """Response with a generic API error."""
    return {
        "ret": ["ERR_CODE::something went wrong"],
        "data": None,
    }


def _total_results_string_ret():
    """Response where totalResults is a string, not int."""
    return {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "totalResults": "500",
            "itemsArray": [
                {
                    "item_id": "111",
                    "title": "Test Product",
                    "price": "10.00",
                    "priceShowWithIcon": {"price": "¥10.00"},
                    "realSales": "100+",
                    "shopInfo": {"title": "Test Shop"},
                }
            ],
        },
    }


# ── Tests ─────────────────────────────────────────────────────────────────


class TestTaobaoSearchNormal:
    """Test normal search responses with valid itemsArray."""

    def test_normal_items_array_with_price_title_sales_shop(self, engine):
        """Normal itemsArray → items list with correct fields populated."""
        engine._mtop = Mock(return_value=_normal_search_ret())

        result = engine.search("华为mate70", limit=10)

        assert result["keyword"] == "华为mate70"
        assert result["total"] == 428
        assert isinstance(result["total"], int)
        assert len(result["items"]) == 3

        # Check first item fields
        item0 = result["items"][0]
        assert item0["title"] == "华为Mate70 Pro 5G智能手机 12+256G 曜金黑"
        assert item0["price"] == "¥6999.00"  # priceShowWithIcon.price takes precedence
        assert item0["origPrice"] == "¥8999.00"
        assert item0["sales"] == "2.3万+"
        assert item0["id"] == "789012345678"
        assert item0["shop"] == "华为官方旗舰店"
        assert item0["url"] == "https://item.taobao.com/item.htm?id=789012345678"

    def test_item_shopinfo_is_none_uses_empty_string(self, engine):
        """If shopInfo is None, shop field should fall back to empty string."""
        engine._mtop = Mock(return_value=_normal_search_ret())

        result = engine.search("华为mate70", limit=10)

        # Third item has shopInfo=None → shop should be ""
        item2 = result["items"][2]
        assert item2["shop"] == ""

    def test_item_no_priceshowwithicon_falls_back_to_price_field(self, engine):
        """If priceShowWithIcon is missing, use the raw price field."""
        ret = {
            "ret": ["SUCCESS::调用成功"],
            "data": {
                "totalResults": 1,
                "itemsArray": [
                    {
                        "item_id": "222",
                        "title": "Bare Item",
                        "price": "99.99",
                        "realSales": "50+",
                    }
                ],
            },
        }
        engine._mtop = Mock(return_value=ret)

        result = engine.search("test", limit=10)
        item = result["items"][0]
        assert item["price"] == "99.99"  # falls back to raw price
        assert item["origPrice"] == ""

    def test_limit_truncates_items(self, engine):
        """limit=2 should return only 2 items from a 3-item response."""
        engine._mtop = Mock(return_value=_normal_search_ret())

        result = engine.search("华为mate70", limit=2)
        assert len(result["items"]) == 2
        assert result["items"][0]["id"] == "789012345678"
        assert result["items"][1]["id"] == "789012345679"


class TestTaobaoSearchEmpty:
    """Test empty / no-results scenarios."""

    def test_itemsarray_missing(self, engine):
        """No itemsArray key → empty items, total=0."""
        engine._mtop = Mock(return_value=_empty_itemsarray_ret())

        result = engine.search("xyz123", limit=10)
        assert result["total"] == 0
        assert result["items"] == []

    def test_itemsarray_empty_list(self, engine):
        """itemsArray present but empty list → empty items."""
        engine._mtop = Mock(return_value=_empty_itemsarray_zero_items_ret())

        result = engine.search("xyz123", limit=10)
        assert result["total"] == 0
        assert result["items"] == []

    def test_itemsarray_explicitly_none(self, engine):
        """itemsArray is None → handled gracefully as empty."""
        ret = {
            "ret": ["SUCCESS::调用成功"],
            "data": {"totalResults": 0, "itemsArray": None},
        }
        engine._mtop = Mock(return_value=ret)

        result = engine.search("test", limit=10)
        assert result["items"] == []


class TestTaobaoSearchErrors:
    """Test error / auth failure handling."""

    def test_fail_sys_token_raises_taobao_auth_error(self, engine):
        """FAIL_SYS_TOKEN in ret → TaobaoAuthError."""
        engine._mtop = Mock(return_value=_fail_token_ret())

        with pytest.raises(TaobaoAuthError) as excinfo:
            engine.search("test", limit=10)
        assert "Session expired" in str(excinfo.value)
        assert "FAIL_SYS_TOKEN" in str(excinfo.value)

    def test_session_expired_raises_taobao_auth_error(self, engine):
        """SESSION_EXPIRED in ret → TaobaoAuthError."""
        engine._mtop = Mock(return_value=_session_expired_ret())

        with pytest.raises(TaobaoAuthError) as excinfo:
            engine.search("test", limit=10)
        assert "Session expired" in str(excinfo.value)

    def test_other_api_error_raises_taobao_api_error(self, engine):
        """Non-auth error in ret → TaobaoAPIError."""
        engine._mtop = Mock(return_value=_api_error_ret())

        with pytest.raises(TaobaoAPIError) as excinfo:
            engine.search("test", limit=10)
        assert "ERR_CODE" in str(excinfo.value)

    def test_ret_is_string_not_list(self, engine):
        """If ret is a string, parsing should still handle it."""
        ret = {
            "ret": "FAIL_SYS_TOKEN::expired",
            "data": {"totalResults": 0, "itemsArray": []},
        }
        engine._mtop = Mock(return_value=ret)

        with pytest.raises(TaobaoAuthError):
            engine.search("test", limit=10)


class TestTaobaoTotalResults:
    """Test totalResults field handling."""

    def test_total_results_is_int_when_response_is_int(self, engine):
        """totalResults is int in response → total is int."""
        engine._mtop = Mock(return_value=_normal_search_ret())

        result = engine.search("test", limit=10)
        assert isinstance(result["total"], int)
        assert result["total"] == 428

    def test_total_results_is_string_converted_to_int(self, engine):
        """totalResults is string '500' → int(total) should work."""
        engine._mtop = Mock(return_value=_total_results_string_ret())

        result = engine.search("test", limit=10)
        assert isinstance(result["total"], int)
        assert result["total"] == 500

    def test_total_results_is_none_defaults_to_zero(self, engine):
        """totalResults missing → int(None) fails, but data.get default=0 saves it."""
        ret = {
            "ret": ["SUCCESS::调用成功"],
            "data": {"itemsArray": []},
        }
        engine._mtop = Mock(return_value=ret)

        result = engine.search("test", limit=10)
        assert result["total"] == 0
