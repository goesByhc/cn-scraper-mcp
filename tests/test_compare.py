"""Unit tests for compare_prices — cross-platform price comparison.

ALL mocks — no real network, filesystem, or Chrome.
"""

from unittest.mock import MagicMock, patch

from cn_scraper_mcp.compare import (
    _PLATFORM_REGISTRY,
    _compute_platform_summary,
    _find_best_deal,
    _normalize_price,
    _search_one_platform,
    compare_prices,
)

# ──────────────────────────────────────────────────────────────────
# Test data helpers
# ──────────────────────────────────────────────────────────────────

def _taobao_items():
    """Mock Taobao search results with prices as strings."""
    return [
        {
            "title": "华为Mate70 Pro 12+256G 曜金黑",
            "price": "¥6999.00",
            "origPrice": "¥8999.00",
            "sales": "2.3万+",
            "id": "789012345678",
            "shop": "华为官方旗舰店",
            "url": "https://item.taobao.com/item.htm?id=789012345678",
        },
        {
            "title": "华为Mate70 RS 非凡大师 16+512G",
            "price": "¥12,999.00",
            "origPrice": "¥15999.00",
            "sales": "1.8万+",
            "id": "789012345679",
            "shop": "华为商城自营",
            "url": "https://item.taobao.com/item.htm?id=789012345679",
        },
        {
            "title": "华为Mate70 标准版 12+256G",
            "price": "¥4,999.00",
            "origPrice": "¥5999.00",
            "sales": "5.2万+",
            "id": "789012345680",
            "shop": "华为旗舰店",
            "url": "https://item.taobao.com/item.htm?id=789012345680",
        },
    ]


def _jd_items():
    """Mock JD search results with float prices."""
    return [
        {
            "sku": "100012345678",
            "name": "华为Mate70 Pro 12+256G 曜金黑",
            "price": 7188.00,
            "ad": False,
            "url": "https://item.jd.com/100012345678.html",
        },
        {
            "sku": "100012345679",
            "name": "华为Mate70 RS 非凡大师 16+512G",
            "price": 13199.00,
            "ad": True,
            "url": "https://item.jd.com/100012345679.html",
        },
    ]


def _pdd_items():
    """Mock PDD search results with string prices (no ¥)."""
    return [
        {
            "goodsId": "888001",
            "name": "华为Mate70 Pro 12+256G 曜金黑",
            "price": "6599.00",
            "sold": "已拼10万+件",
            "url": "https://mobile.yangkeduo.com/goods2.html?goodsId=888001",
        },
        {
            "goodsId": "888002",
            "name": "华为Mate70 标准版 12+256G",
            "price": "4599.00",
            "sold": "已拼8万+件",
            "url": "https://mobile.yangkeduo.com/goods2.html?goodsId=888002",
        },
    ]


def _taobao_result(items: list = None):
    """Build a full Taobao search result dict."""
    if items is None:
        items = _taobao_items()
    return {"keyword": "华为mate70", "total": len(items), "items": items}


def _jd_result(items: list = None):
    """Build a full JD search result dict."""
    if items is None:
        items = _jd_items()
    return {"keyword": "华为mate70", "count": len(items), "items": items, "state": "ok"}


def _pdd_result(items: list = None):
    """Build a full PDD search result dict."""
    if items is None:
        items = _pdd_items()
    return {"keyword": "华为mate70", "count": len(items), "items": items}


# ──────────────────────────────────────────────────────────────────
# Tests: _normalize_price
# ──────────────────────────────────────────────────────────────────


class TestNormalizePrice:
    """Test price normalization from various input formats."""

    def test_none_returns_none(self):
        assert _normalize_price(None) is None

    def test_int(self):
        assert _normalize_price(6999) == 6999.0

    def test_float(self):
        assert _normalize_price(7188.0) == 7188.0

    def test_string_simple(self):
        assert _normalize_price("6999.00") == 6999.0

    def test_string_with_yen_sign(self):
        assert _normalize_price("¥6999.00") == 6999.0

    def test_string_with_rmb_yen(self):
        assert _normalize_price("￥6999") == 6999.0

    def test_string_with_comma_and_yen(self):
        assert _normalize_price("¥12,999.00") == 12999.0

    def test_string_with_spaces(self):
        assert _normalize_price("¥ 6,999.00") == 6999.0

    def test_string_with_yuan(self):
        assert _normalize_price("6999 元") == 6999.0

    def test_string_with_dollar(self):
        assert _normalize_price("$999.99") == 999.99

    def test_garbage_string_returns_none(self):
        assert _normalize_price("not a price") is None

    def test_empty_string_returns_none(self):
        assert _normalize_price("") is None


# ──────────────────────────────────────────────────────────────────
# Tests: _compute_platform_summary
# ──────────────────────────────────────────────────────────────────


class TestComputePlatformSummary:
    """Test price_range and median calculation."""

    def test_normal_items(self):
        summary = _compute_platform_summary(_taobao_items(), "price")
        assert summary["price_range"] == [4999.0, 12999.0]
        assert summary["median"] == 6999.0

    def test_empty_items(self):
        summary = _compute_platform_summary([], "price")
        assert summary["price_range"] == [None, None]
        assert summary["median"] is None

    def test_items_with_unparseable_prices(self):
        items = [
            {"price": "invalid"},
            {"price": None},
            {"price": "¥6,999.00"},
        ]
        summary = _compute_platform_summary(items, "price")
        assert summary["price_range"] == [6999.0, 6999.0]
        assert summary["median"] == 6999.0


# ──────────────────────────────────────────────────────────────────
# Tests: _find_best_deal
# ──────────────────────────────────────────────────────────────────


class TestFindBestDeal:
    """Test best deal calculation across platforms."""

    def test_finds_cheapest_item(self):
        platforms_data = {
            "taobao": {
                "status": "ok",
                "items": _taobao_items(),
            },
            "jd": {
                "status": "ok",
                "items": _jd_items(),
            },
        }
        best = _find_best_deal(platforms_data, _PLATFORM_REGISTRY)
        assert best is not None
        assert best["platform"] == "taobao"
        assert best["price"] == 4999.0
        assert "Mate70" in best["title"]

    def test_one_platform_failed_still_finds_deal(self):
        platforms_data = {
            "taobao": {
                "status": "error",
                "error": "Cookie expired",
                "items": [],
            },
            "jd": {
                "status": "ok",
                "items": _jd_items(),
            },
        }
        best = _find_best_deal(platforms_data, _PLATFORM_REGISTRY)
        assert best is not None
        assert best["platform"] == "jd"
        assert best["price"] == 7188.0

    def test_all_platforms_failed_returns_none(self):
        platforms_data = {
            "taobao": {"status": "error", "error": "down", "items": []},
            "jd": {"status": "error", "error": "down", "items": []},
        }
        best = _find_best_deal(platforms_data, _PLATFORM_REGISTRY)
        assert best is None

    def test_empty_items_returns_none(self):
        platforms_data = {
            "taobao": {"status": "ok", "items": []},
            "jd": {"status": "ok", "items": []},
        }
        best = _find_best_deal(platforms_data, _PLATFORM_REGISTRY)
        assert best is None


# ──────────────────────────────────────────────────────────────────
# Tests: _search_one_platform (unit)
# ──────────────────────────────────────────────────────────────────


class TestSearchOnePlatform:
    """Test single-platform search wrapper."""

    def test_taobao_success(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_engine = MagicMock()
            mock_engine.search.return_value = _taobao_result()
            mock_mod.TaobaoEngine.return_value = mock_engine
            mock_import.return_value = mock_mod

            result = _search_one_platform("taobao", "华为mate70", limit=3)

        assert result["status"] == "ok"
        assert len(result["items"]) == 3
        assert result["price_range"] == [4999.0, 12999.0]

    def test_jd_success(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_engine = MagicMock()
            mock_engine.search.return_value = _jd_result()
            mock_mod.JDEngine.return_value = mock_engine
            mock_import.return_value = mock_mod

            result = _search_one_platform("jd", "华为mate70", limit=2)

        assert result["status"] == "ok"
        assert len(result["items"]) == 2
        assert result["price_range"] == [7188.0, 13199.0]

    def test_engine_raises_exception(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_engine_cls = MagicMock()
            mock_engine_cls.side_effect = RuntimeError("Boom")
            mock_mod.JDEngine = mock_engine_cls
            mock_import.return_value = mock_mod

            result = _search_one_platform("jd", "test", limit=5)

        assert result["status"] == "error"
        assert "Boom" in result["error"]
        assert result["items"] == []

    def test_engine_returns_error_dict(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_engine = MagicMock()
            mock_engine.search.return_value = {"error": "无法启动京东浏览器", "hint": "..."}
            mock_mod.JDEngine.return_value = mock_engine
            mock_import.return_value = mock_mod

            result = _search_one_platform("jd", "test", limit=5)

        assert result["status"] == "error"
        assert "无法启动京东浏览器" in result["error"]

    def test_unknown_platform(self):
        result = _search_one_platform("unknown", "test", limit=5)
        assert result["status"] == "error"
        assert "Unknown platform" in result["error"]


# ──────────────────────────────────────────────────────────────────
# Tests: compare_prices (integration)
# ──────────────────────────────────────────────────────────────────


class TestComparePricesBothSucceed:
    """Test compare_prices when all platforms succeed."""

    def test_both_succeed(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_tb_mod = MagicMock()
            mock_tb_eng = MagicMock()
            mock_tb_eng.search.return_value = _taobao_result()
            mock_tb_mod.TaobaoEngine.return_value = mock_tb_eng

            mock_jd_mod = MagicMock()
            mock_jd_eng = MagicMock()
            mock_jd_eng.search.return_value = _jd_result()
            mock_jd_mod.JDEngine.return_value = mock_jd_eng

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb_mod
                if "jd" in name:
                    return mock_jd_mod
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("华为mate70")

        assert result["keyword"] == "华为mate70"
        assert "taobao" in result["platforms"]
        assert "jd" in result["platforms"]

        tb = result["platforms"]["taobao"]
        assert tb["status"] == "ok"
        assert len(tb["items"]) == 3

        jd = result["platforms"]["jd"]
        assert jd["status"] == "ok"
        assert len(jd["items"]) == 2

        # Best deal should be the cheapest taobao item at ¥4999
        assert result["best_deal"] is not None
        assert result["best_deal"]["price"] == 4999.0


class TestComparePricesPartialFailure:
    """Test compare_prices when one platform fails."""

    def test_taobao_fails_jd_succeeds(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            # Taobao → crash
            mock_tb_mod = MagicMock()
            mock_tb_mod.TaobaoEngine.side_effect = RuntimeError("Taobao API down")

            # JD → success
            mock_jd_mod = MagicMock()
            mock_jd_eng = MagicMock()
            mock_jd_eng.search.return_value = _jd_result()
            mock_jd_mod.JDEngine.return_value = mock_jd_eng

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb_mod
                if "jd" in name:
                    return mock_jd_mod
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("华为mate70")

        # Taobao should show error
        assert result["platforms"]["taobao"]["status"] == "error"
        assert result["platforms"]["jd"]["status"] == "ok"
        # Best deal from JD only
        assert result["best_deal"] is not None
        assert result["best_deal"]["platform"] == "jd"

    def test_jd_fails_taobao_succeeds(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_tb_mod = MagicMock()
            mock_tb_eng = MagicMock()
            mock_tb_eng.search.return_value = _taobao_result()
            mock_tb_mod.TaobaoEngine.return_value = mock_tb_eng

            mock_jd_mod = MagicMock()
            mock_jd_eng = MagicMock()
            mock_jd_eng.search.return_value = {"error": "JD down"}
            mock_jd_mod.JDEngine.return_value = mock_jd_eng

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb_mod
                if "jd" in name:
                    return mock_jd_mod
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("华为mate70")

        assert result["platforms"]["taobao"]["status"] == "ok"
        assert result["platforms"]["jd"]["status"] == "error"
        assert result["best_deal"]["platform"] == "taobao"


class TestComparePricesEmptyResults:
    """Test compare_prices with empty results from one platform."""

    def test_one_platform_empty(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_tb_mod = MagicMock()
            mock_tb_eng = MagicMock()
            mock_tb_eng.search.return_value = _taobao_result()
            mock_tb_mod.TaobaoEngine.return_value = mock_tb_eng

            mock_jd_mod = MagicMock()
            mock_jd_eng = MagicMock()
            mock_jd_eng.search.return_value = _jd_result(items=[])
            mock_jd_mod.JDEngine.return_value = mock_jd_eng

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb_mod
                if "jd" in name:
                    return mock_jd_mod
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("rare-item-xyz")

        assert result["platforms"]["taobao"]["status"] == "ok"
        assert result["platforms"]["jd"]["status"] == "ok"
        assert result["platforms"]["jd"]["items"] == []
        # Best deal should come from taobao only
        assert result["best_deal"] is not None
        assert result["best_deal"]["platform"] == "taobao"


class TestComparePricesCustomPlatforms:
    """Test compare_prices with custom platform lists."""

    def test_single_platform(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_eng = MagicMock()
            mock_eng.search.return_value = _taobao_result()
            mock_mod.TaobaoEngine.return_value = mock_eng
            mock_import.return_value = mock_mod

            result = compare_prices("test", platforms=["taobao"])

        assert list(result["platforms"].keys()) == ["taobao"]

    def test_all_three_platforms(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            # Taobao
            mock_tb = MagicMock()
            mock_tb.TaobaoEngine.return_value.search.return_value = _taobao_result()
            # JD
            mock_jd = MagicMock()
            mock_jd.JDEngine.return_value.search.return_value = _jd_result()
            # PDD
            mock_pdd = MagicMock()
            mock_pdd.PDDEngine.return_value.search.return_value = _pdd_result()

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb
                if "jd" in name:
                    return mock_jd
                if "pdd" in name:
                    return mock_pdd
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("test", platforms=["taobao", "jd", "pdd"])

        assert set(result["platforms"].keys()) == {"taobao", "jd", "pdd"}
        for p in ["taobao", "jd", "pdd"]:
            assert result["platforms"][p]["status"] == "ok"
        # PDD has the cheapest (¥4599)
        assert result["best_deal"]["platform"] == "pdd"
        assert result["best_deal"]["price"] == 4599.0

    def test_deduplicates_platforms(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_mod = MagicMock()
            mock_eng = MagicMock()
            mock_eng.search.return_value = _taobao_result()
            mock_mod.TaobaoEngine.return_value = mock_eng
            mock_import.return_value = mock_mod

            result = compare_prices("test", platforms=["taobao", "taobao", "jd"])

        # Should not search taobao twice
        assert list(result["platforms"].keys()) == ["taobao", "jd"]


class TestComparePricesPriceNormalization:
    """Test that price normalization works in the full comparison flow."""

    def test_mixed_price_formats(self):
        """Taobao uses ¥-prefixed strings, JD uses floats, both normalized."""
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            # Taobao with ¥-string prices
            mock_tb = MagicMock()
            mock_tb.TaobaoEngine.return_value.search.return_value = _taobao_result()
            # JD with float prices
            mock_jd = MagicMock()
            mock_jd.JDEngine.return_value.search.return_value = _jd_result()

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb
                if "jd" in name:
                    return mock_jd
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("华为mate70", platforms=["taobao", "jd"])

        # Prices should all be normalized to float
        tb_range = result["platforms"]["taobao"]["price_range"]
        assert tb_range[0] == 4999.0
        assert tb_range[1] == 12999.0

        jd_range = result["platforms"]["jd"]["price_range"]
        assert jd_range[0] == 7188.0
        assert jd_range[1] == 13199.0

        # Best deal price is a clean float
        assert isinstance(result["best_deal"]["price"], float)
        assert result["best_deal"]["price"] == 4999.0


class TestComparePricesDefaultPlatforms:
    """Test default platform behavior."""

    def test_defaults_to_taobao_and_jd(self):
        with patch("cn_scraper_mcp.compare.importlib.import_module") as mock_import:
            mock_tb = MagicMock()
            mock_tb.TaobaoEngine.return_value.search.return_value = _taobao_result()
            mock_jd = MagicMock()
            mock_jd.JDEngine.return_value.search.return_value = _jd_result()

            def _import_side_effect(name, *args, **kwargs):
                if "taobao" in name:
                    return mock_tb
                if "jd" in name:
                    return mock_jd
                raise ImportError(name)

            mock_import.side_effect = _import_side_effect

            result = compare_prices("test")

        assert set(result["platforms"].keys()) == {"taobao", "jd"}
