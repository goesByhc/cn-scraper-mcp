"""Unit tests for aggregate cross-platform search & trending.

ALL mocks — no real network, filesystem, or Chrome.
Tests cover: concurrency, partial failure, timeout, platform whitelist.
"""

import time
from unittest.mock import MagicMock, patch

from cn_scraper_mcp.aggregate import (
    _hotlist_one,
    _run_concurrent,
    _search_one,
    get_trending,
    search_all,
    search_content,
    search_products,
)

# ═══════════════════════════════════════════════════════════════
# Mock item data
# ═══════════════════════════════════════════════════════════════


def _taobao_search_result():
    return {
        "keyword": "mate70",
        "total": 2,
        "items": [
            {"title": "华为Mate70 Pro", "price": "¥6999.00", "origPrice": "¥8999.00",
             "sales": "2.3万+", "id": "78901", "shop": "旗舰店",
             "url": "https://item.taobao.com/item.htm?id=78901"},
            {"title": "华为Mate70 RS", "price": "¥12999.00", "origPrice": "¥15999.00",
             "sales": "1.8万+", "id": "78902", "shop": "商城自营",
             "url": "https://item.taobao.com/item.htm?id=78902"},
        ],
    }


def _weibo_hot_result():
    return {
        "count": 2,
        "items": [
            {"rank": 1, "word": "华为Mate70发布", "num": 1105077,
             "url": "https://s.weibo.com/weibo?q=test", "label": "爆"},
            {"rank": 2, "word": "高考分数线", "num": 980000,
             "url": "https://s.weibo.com/weibo?q=test2", "label": ""},
        ],
    }


def _zhihu_hot_result():
    return {
        "items": [
            {"title": "华为发布Mate70", "url": "https://zhihu.com/q/1",
             "hot_metric": "1024万热度", "excerpt": "华为今日发布..."},
            {"title": "AI行业新突破", "url": "https://zhihu.com/q/2",
             "hot_metric": "890万热度", "excerpt": "新算法..."},
        ],
    }


# ═══════════════════════════════════════════════════════════════
# Tests: _search_one (single platform)
# ═══════════════════════════════════════════════════════════════


class TestSearchOne:
    def test_unknown_platform(self):
        result = _search_one("nonexistent", "test", 5)
        assert result["status"] == "error"
        assert "Unknown platform" in result["error"]

    def test_engine_returns_error_dict(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {"error": "some error", "hint": "do this"}

        with patch("cn_scraper_mcp.engines.taobao.TaobaoEngine",
                   return_value=mock_engine):
            result = _search_one("taobao", "test", 5)
            assert result["status"] == "error"
            assert result["error"] == "some error"

    def test_engine_raises_exception(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = RuntimeError("boom")

        with patch("cn_scraper_mcp.engines.taobao.TaobaoEngine",
                   return_value=mock_engine):
            result = _search_one("taobao", "test", 5)
            assert result["status"] == "error"
            assert "boom" in result["error"]

    def test_successful_search_normalizes_items(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = _taobao_search_result()

        with patch("cn_scraper_mcp.engines.taobao.TaobaoEngine",
                   return_value=mock_engine):
            result = _search_one("taobao", "mate70", 5)
            assert result["status"] == "ok"
            assert len(result["items"]) == 2
            item = result["items"][0]
            assert item["platform"] == "taobao"
            assert item["type"] == "product"
            assert item["price"] == 6999.0
            assert item["title"] == "华为Mate70 Pro"


# ═══════════════════════════════════════════════════════════════
# Tests: _hotlist_one (single platform)
# ═══════════════════════════════════════════════════════════════


class TestHotlistOne:
    def test_unknown_platform(self):
        result = _hotlist_one("unknown")
        assert result["status"] == "error"

    def test_weibo_hotlist_normalizes(self):
        mock_engine = MagicMock()
        mock_engine.hot_list.return_value = _weibo_hot_result()

        with patch("cn_scraper_mcp.engines.weibo.WeiboEngine",
                   return_value=mock_engine):
            result = _hotlist_one("weibo")
            assert result["status"] == "ok"
            assert len(result["items"]) == 2
            item = result["items"][0]
            assert item["platform"] == "weibo"
            assert item["rank"] == 1
            assert item["word"] == "华为Mate70发布"
            assert item["label"] == "爆"

    def test_zhihu_hotlist_normalizes(self):
        mock_engine = MagicMock()
        mock_engine.hot_list.return_value = _zhihu_hot_result()

        with patch("cn_scraper_mcp.engines.zhihu.ZhihuEngine",
                   return_value=mock_engine):
            result = _hotlist_one("zhihu")
            assert result["status"] == "ok"
            assert len(result["items"]) == 2
            item = result["items"][0]
            assert item["platform"] == "zhihu"
            assert item["rank"] == 1

    def test_engine_error_in_result(self):
        mock_engine = MagicMock()
        mock_engine.hot_list.return_value = {"error": "needs login"}

        with patch("cn_scraper_mcp.engines.weibo.WeiboEngine",
                   return_value=mock_engine):
            result = _hotlist_one("weibo")
            assert result["status"] == "error"
            assert result["error"] == "needs login"


# ═══════════════════════════════════════════════════════════════
# Tests: _run_concurrent (thread-pool executor)
# ═══════════════════════════════════════════════════════════════


class TestRunConcurrent:
    def test_all_platforms_succeed(self):
        def fake_search(p, kw, lim, deadline=None):
            return {"status": "ok", "items": [{"platform": p, "title": kw}]}

        results = _run_concurrent(fake_search, ["taobao", "jd", "pdd"], "test", 5, timeout=10)
        assert len(results) == 3
        for p in ["taobao", "jd", "pdd"]:
            assert results[p]["status"] == "ok"
            assert results[p]["items"][0]["platform"] == p

    def test_partial_failure(self):
        fail_platforms = {"jd"}

        def fake_search(p, kw, lim, deadline=None):
            if p in fail_platforms:
                raise RuntimeError("platform down")
            return {"status": "ok", "items": [{"platform": p}]}

        results = _run_concurrent(fake_search, ["taobao", "jd", "pdd"], "test", 5, timeout=10)
        assert len(results) == 3
        assert results["taobao"]["status"] == "ok"
        assert results["pdd"]["status"] == "ok"
        assert results["jd"]["status"] == "error"

    def test_all_fail(self):
        def fake_search(p, kw, lim, deadline=None):
            raise RuntimeError("all down")

        results = _run_concurrent(fake_search, ["taobao", "jd"], "test", 5, timeout=10)
        assert len(results) == 2
        assert results["taobao"]["status"] == "error"
        assert results["jd"]["status"] == "error"

    def test_timeout_flag(self):
        """Futures that don't complete within the global deadline get 'Timeout'."""
        def slow_search(p, kw, lim, deadline=None):
            time.sleep(0.5)
            return {"status": "ok", "items": []}

        # Very short timeout — some futures should get marked "Timeout"
        started = time.perf_counter()
        results = _run_concurrent(slow_search, ["taobao", "jd", "pdd"], "test", 5, timeout=0.1)
        elapsed = time.perf_counter() - started
        # At least one should have timed out
        errors = [v for v in results.values() if v.get("status") == "error"]
        assert len(errors) > 0
        assert elapsed < 0.3

    def test_single_platform(self):
        def fake_search(p, kw, lim, deadline=None):
            return {"status": "ok", "items": []}

        results = _run_concurrent(fake_search, ["taobao"], "test", 5, timeout=10)
        assert len(results) == 1
        assert results["taobao"]["status"] == "ok"


# ═══════════════════════════════════════════════════════════════
# Tests: search_all (top-level API)
# ═══════════════════════════════════════════════════════════════


class TestSearchAll:
    def test_all_keyword_search_platforms(self):
        """Verify search_all dispatches only to engines with search()."""
        with patch("cn_scraper_mcp.aggregate._search_one") as mock_search:
            mock_search.return_value = {"status": "ok", "items": []}
            result = search_all("mate70", timeout=10)

            assert result["keyword"] == "mate70"
            called_platforms = {call[0][0] for call in mock_search.call_args_list}
            assert len(called_platforms) == 7
            assert called_platforms == {
                "taobao", "jd", "pdd", "xiaohongshu", "zhihu", "weibo", "douyin",
            }

    def test_platform_whitelist(self):
        with patch("cn_scraper_mcp.aggregate._search_one") as mock_search:
            mock_search.return_value = {"status": "ok", "items": []}
            search_all("mate70", platforms=["taobao", "jd"], timeout=10)

            called_platforms = {call[0][0] for call in mock_search.call_args_list}
            assert called_platforms == {"taobao", "jd"}

    def test_partial_failure_structure(self):
        """One platform failing should not block others in the output."""
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "taobao": {"status": "ok", "items": []},
                "jd": {"status": "error", "error": "browser not found"},
                "pdd": {"status": "ok", "items": []},
            }
            result = search_all("mate70", platforms=["taobao", "jd", "pdd"], timeout=10)

            assert result["platforms"]["taobao"]["status"] == "ok"
            assert result["platforms"]["jd"]["status"] == "error"
            assert result["platforms"]["pdd"]["status"] == "ok"

    def test_invalid_platforms_filtered(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("mate70", platforms=["taobao", "nonexistent", "jd"], timeout=10)

            called_platforms = mock_run.call_args[0][1]
            assert "nonexistent" not in called_platforms
            assert "taobao" in called_platforms
            assert "jd" in called_platforms


# ═══════════════════════════════════════════════════════════════
# Tests: search_products
# ═══════════════════════════════════════════════════════════════


class TestSearchProducts:
    def test_ecommerce_only(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_products("mate70")

            called = mock_run.call_args[0][1]
            assert set(called) == {"taobao", "jd", "pdd"}

    def test_platform_whitelist_filtered_to_ecom(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_products("mate70", platforms=["taobao", "weibo", "jd"])

            called = mock_run.call_args[0][1]
            assert "weibo" not in called
            assert set(called) == {"taobao", "jd"}

    def test_price_comparison(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "taobao": {
                    "status": "ok",
                    "items": [
                        {"title": "A", "price": 6999.0},
                        {"title": "B", "price": 12999.0},
                    ],
                },
                "jd": {
                    "status": "ok",
                    "items": [
                        {"title": "C", "price": 7188.0},
                    ],
                },
                "pdd": {"status": "error", "error": "rate limited"},
            }
            result = search_products("mate70")

            pc = result["price_comparison"]
            assert pc["price_range"][0] == 6999.0
            assert pc["price_range"][1] == 12999.0
            assert pc["median"] == 7188.0

    def test_price_comparison_with_none_prices(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "taobao": {
                    "status": "ok",
                    "items": [
                        {"title": "A", "price": None},
                        {"title": "B", "price": 100.0},
                    ],
                },
                "jd": {"status": "ok", "items": []},
                "pdd": {"status": "error", "error": "fail"},
            }
            result = search_products("mate70")

            pc = result["price_comparison"]
            assert pc["price_range"] == [100.0, 100.0]
            assert pc["median"] == 100.0

    def test_price_comparison_all_errors(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "taobao": {"status": "error", "error": "fail"},
                "jd": {"status": "error", "error": "fail"},
                "pdd": {"status": "error", "error": "fail"},
            }
            result = search_products("mate70")

            pc = result["price_comparison"]
            assert pc["price_range"] == [None, None]
            assert pc["median"] is None


# ═══════════════════════════════════════════════════════════════
# Tests: search_content
# ═══════════════════════════════════════════════════════════════


class TestSearchContent:
    def test_content_only(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_content("mate70")

            called = mock_run.call_args[0][1]
            assert set(called) == {"xiaohongshu", "zhihu", "weibo", "douyin"}

    def test_platform_whitelist_filtered_to_content(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_content("mate70", platforms=["xiaohongshu", "taobao", "zhihu"])

            called = mock_run.call_args[0][1]
            assert "taobao" not in called
            assert set(called) == {"xiaohongshu", "zhihu"}

    def test_result_structure(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "xiaohongshu": {"status": "ok", "items": []},
                "zhihu": {"status": "error", "error": "needs login"},
            }
            result = search_content("mate70", platforms=["xiaohongshu", "zhihu"])

            assert result["keyword"] == "mate70"
            assert result["platforms"]["xiaohongshu"]["status"] == "ok"
            assert result["platforms"]["zhihu"]["status"] == "error"


# ═══════════════════════════════════════════════════════════════
# Tests: get_trending
# ═══════════════════════════════════════════════════════════════


class TestGetTrending:
    def test_all_hot_platforms(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            get_trending()

            called = mock_run.call_args[0][1]
            assert set(called) == {"weibo", "zhihu", "douyin"}

    def test_platform_whitelist(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            get_trending(platforms=["weibo", "zhihu"])

            called = mock_run.call_args[0][1]
            assert set(called) == {"weibo", "zhihu"}

    def test_invalid_platforms_filtered(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            get_trending(platforms=["weibo", "taobao", "douyin"])

            called = mock_run.call_args[0][1]
            assert "taobao" not in called
            assert set(called) == {"weibo", "douyin"}

    def test_partial_failure(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {
                "weibo": {"status": "ok", "items": [{"rank": 1, "word": "热搜"}]},
                "zhihu": {"status": "error", "error": "needs login"},
                "douyin": {"status": "ok", "items": [{"rank": 1, "word": "热榜"}]},
            }
            result = get_trending()

            assert result["platforms"]["weibo"]["status"] == "ok"
            assert result["platforms"]["zhihu"]["status"] == "error"
            assert result["platforms"]["douyin"]["status"] == "ok"

    def test_result_structure(self):
        """Top-level key is 'platforms', not 'keyword'."""
        result = get_trending(platforms=["weibo"])
        assert "platforms" in result
        assert "keyword" not in result


# ═══════════════════════════════════════════════════════════════
# Tests: concurrency (real ThreadPoolExecutor)
# ═══════════════════════════════════════════════════════════════


class TestRealConcurrency:
    def test_concurrent_execution(self):
        """Verify that platforms are actually executed in parallel."""
        def timed_search(p, kw, lim, deadline=None):
            if p == "taobao":
                time.sleep(0.2)
            elif p == "jd":
                time.sleep(0.15)
            else:
                time.sleep(0.1)
            return {"status": "ok", "items": [{"platform": p}]}

        t0 = time.monotonic()
        results = _run_concurrent(timed_search, ["taobao", "jd", "pdd"], "test", 5, timeout=10)
        total = time.monotonic() - t0

        assert total < 0.5, f"Expected concurrent execution (~0.2s), got {total:.2f}s"
        assert len(results) == 3
        for p in ["taobao", "jd", "pdd"]:
            assert results[p]["status"] == "ok"

    def test_concurrent_partial_failure_isolation(self):
        """A slow platform should not block fast platforms."""
        def mixed_search(p, kw, lim, deadline=None):
            if p == "slow":
                time.sleep(0.5)
                return {"status": "ok", "items": [{"platform": p}]}
            elif p == "fast":
                return {"status": "ok", "items": [{"platform": p}]}
            else:
                raise RuntimeError("boom")

        results = _run_concurrent(mixed_search, ["fast", "slow", "bad"], "test", 5, timeout=5)

        assert results["fast"]["status"] == "ok"
        assert results["slow"]["status"] == "ok"
        assert results["bad"]["status"] == "error"


# ═══════════════════════════════════════════════════════════════
# Tests: edge cases
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_empty_platforms_list(self):
        """Empty platform list should fall back to defaults."""
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("test", platforms=[])

            called = mock_run.call_args[0][1]
            assert len(called) == 7

    def test_all_invalid_platforms(self):
        """All invalid platforms should fall back to defaults."""
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("test", platforms=["foo", "bar"])

            called = mock_run.call_args[0][1]
            assert len(called) == 7

    def test_deduplicate_platforms(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("test", platforms=["taobao", "taobao", "jd"])

            called = mock_run.call_args[0][1]
            assert called == ["taobao", "jd"]

    def test_limit_is_passed_through(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("test", limit=7)

            assert mock_run.call_args[0][3] == 7

    def test_keyword_is_passed_through(self):
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            search_all("special keyword!", timeout=5)

            assert mock_run.call_args[0][2] == "special keyword!"

    def test_get_trending_all_invalid_platforms(self):
        """All invalid → default."""
        with patch("cn_scraper_mcp.aggregate._run_concurrent") as mock_run:
            mock_run.return_value = {}
            get_trending(platforms=["foo", "bar"])

            called = mock_run.call_args[0][1]
            assert set(called) == {"weibo", "zhihu", "douyin"}
