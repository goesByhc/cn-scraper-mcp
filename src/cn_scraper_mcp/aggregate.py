"""Aggregated cross-platform search & trending — concurrent, partial-failure-tolerant.

Exposes four functions that search multiple platforms in parallel:
  - search_all       — all keyword-search platforms (or whitelist) concurrently
  - search_products  — e-commerce platforms (taobao/jd/pdd)
  - search_content   — content platforms (xiaohongshu/zhihu/weibo/douyin)
  - get_trending     — aggregate hot lists (weibo/zhihu/douyin)

Design principles:
  - Thread-pool concurrency (engines are synchronous, not async)
  - Per-platform timeout + global timeout via futures
  - Single-platform failure → independent error entry; other platforms still succeed
  - All results normalized to ProductItem / ContentItem / TrendItem via models.py
"""

from __future__ import annotations

import concurrent.futures
import importlib
import statistics
import time
from dataclasses import asdict
from typing import Any

from cn_scraper_mcp.dedup import cluster_products, deduplicate
from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.aggregate")

# ═══════════════════════════════════════════════════════════════
# Platform registry
# ═══════════════════════════════════════════════════════════════

_SEARCH_REGISTRY: dict[str, dict[str, Any]] = {
    "taobao": {
        "module": "cn_scraper_mcp.engines.taobao",
        "class": "TaobaoEngine",
        "normalize": "cn_scraper_mcp.models.normalize_taobao",
    },
    "jd": {
        "module": "cn_scraper_mcp.engines.jd",
        "class": "JDEngine",
        "normalize": "cn_scraper_mcp.models.normalize_jd",
    },
    "pdd": {
        "module": "cn_scraper_mcp.engines.pdd",
        "class": "PDDEngine",
        "normalize": "cn_scraper_mcp.models.normalize_pdd",
    },
    "xiaohongshu": {
        "module": "cn_scraper_mcp.engines.xiaohongshu",
        "class": "XiaohongshuEngine",
        "normalize": "cn_scraper_mcp.models.normalize_xiaohongshu",
    },
    "zhihu": {
        "module": "cn_scraper_mcp.engines.zhihu",
        "class": "ZhihuEngine",
        "normalize": "cn_scraper_mcp.models.normalize_zhihu",
    },
    "weibo": {
        "module": "cn_scraper_mcp.engines.weibo",
        "class": "WeiboEngine",
        "normalize": "cn_scraper_mcp.models.normalize_weibo",
    },
    "douyin": {
        "module": "cn_scraper_mcp.engines.douyin",
        "class": "DouyinEngine",
        "normalize": "cn_scraper_mcp.models.normalize_douyin",
    },
}

_HOTLIST_REGISTRY: dict[str, dict[str, Any]] = {
    "weibo": {
        "module": "cn_scraper_mcp.engines.weibo",
        "class": "WeiboEngine",
        "normalize": "cn_scraper_mcp.models.normalize_weibo_hot",
    },
    "zhihu": {
        "module": "cn_scraper_mcp.engines.zhihu",
        "class": "ZhihuEngine",
        "normalize": "cn_scraper_mcp.models.normalize_zhihu_hot",
    },
    "douyin": {
        "module": "cn_scraper_mcp.engines.douyin",
        "class": "DouyinEngine",
        "normalize": "cn_scraper_mcp.models.normalize_douyin_hot",
    },
}

ALL_PLATFORMS = sorted(_SEARCH_REGISTRY.keys())
ECOMMERCE_PLATFORMS = ["taobao", "jd", "pdd"]
CONTENT_PLATFORMS = ["xiaohongshu", "zhihu", "weibo", "douyin"]
HOTLIST_PLATFORMS = sorted(_HOTLIST_REGISTRY.keys())

# ═══════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════


def _resolve_platforms(
    requested: list[str] | None,
    valid: list[str],
    default: list[str],
) -> list[str]:
    """Filter and deduplicate a platform whitelist.

    Returns deduplicated list of valid platform names.
    Falls back to *default* if requested is None or empty after filtering.
    """
    if not requested:
        return list(default)
    seen: set[str] = set()
    out: list[str] = []
    for p in requested:
        if p in valid and p not in seen:
            seen.add(p)
            out.append(p)
    return out or list(default)


def _import_fn(qualname: str) -> Any:
    """Import a function from a dotted path like 'module.path.func_name'."""
    mod_name, _, fn_name = qualname.rpartition(".")
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def _search_one(
    platform: str,
    keyword: str,
    limit: int,
    deadline: float | None = None,
) -> dict:
    """Search a single platform and return normalized result dict.

    Returns:
        {"status": "ok", "items": [...]}  or  {"status": "error", "error": "..."}
    """
    config = _SEARCH_REGISTRY.get(platform)
    if config is None:
        return {"status": "error", "error": f"Unknown platform: {platform}"}

    try:
        mod = importlib.import_module(config["module"])
        engine_cls = getattr(mod, config["class"])
        engine = engine_cls()

        # Check global deadline before making the expensive engine call
        if deadline is not None and time.monotonic() > deadline:
            return {"status": "error", "error": "Timeout"}

        raw_result = engine.search(keyword, limit=limit)

        # Handle engine-level error returns
        if isinstance(raw_result, dict) and "error" in raw_result and "items" not in raw_result:
            return {
                "status": "error",
                "error": raw_result.get("error", "Unknown engine error"),
            }

        raw_items = raw_result.get("items", [])
        normalize = _import_fn(config["normalize"])
        normalized = [asdict(normalize(item)) for item in raw_items]

        return {"status": "ok", "items": normalized}
    except Exception as e:
        logger.warning("Platform '%s' search failed: %s", platform, e)
        return {"status": "error", "error": str(e)}

def _hotlist_one(platform: str, deadline: float | None = None) -> dict:
    """Fetch hot list from a single platform and return normalized result dict.

    Returns:
        {"status": "ok", "items": [...]}  or  {"status": "error", "error": "..."}
    """
    config = _HOTLIST_REGISTRY.get(platform)
    if config is None:
        return {"status": "error", "error": f"Unknown platform: {platform}"}

    try:
        mod = importlib.import_module(config["module"])
        engine_cls = getattr(mod, config["class"])
        engine = engine_cls()

        # Check global deadline before making the expensive engine call
        if deadline is not None and time.monotonic() > deadline:
            return {"status": "error", "error": "Timeout"}

        raw_result = engine.hot_list()

        if isinstance(raw_result, dict) and "error" in raw_result and "items" not in raw_result:
            return {
                "status": "error",
                "error": raw_result.get("error", "Unknown engine error"),
            }

        raw_items = raw_result.get("items", [])
        normalize = _import_fn(config["normalize"])
        # Some hot-list normalizers take a rank argument
        normalized = []
        for idx, item in enumerate(raw_items):
            rank = idx + 1
            norm = normalize(item) if platform == "weibo" else normalize(item, rank=rank)
            normalized.append(asdict(norm))

        return {"status": "ok", "items": normalized}
    except Exception as e:
        logger.warning("Platform '%s' hot_list failed: %s", platform, e)
        return {"status": "error", "error": str(e)}


def _run_concurrent(
    fn: Any,
    platforms: list[str],
    keyword: str | None,
    limit: int,
    timeout: float,
) -> dict[str, dict]:
    """Execute *fn* for each platform concurrently using a thread pool.

    Each platform gets its own future. The global *timeout* is enforced
    via as_completed with a deadline. Partial failures are tolerated —
    error entries are included in the result dict for failed platforms.
    """
    results: dict[str, dict] = {}
    max_workers = max(1, len(platforms))

    # Absolute deadline for workers to check before expensive engine calls.
    # This prevents threads that start after the global timeout from doing
    # unnecessary work that callers are no longer interested in.
    worker_deadline = time.monotonic() + timeout if timeout > 0 else None

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures: dict[concurrent.futures.Future, str] = {}
        for p in platforms:
            if keyword is not None:
                fut = executor.submit(fn, p, keyword, limit, worker_deadline)
            else:
                fut = executor.submit(fn, p, worker_deadline)
            futures[fut] = p

        deadline = timeout if timeout > 0 else None

        try:
            for fut in concurrent.futures.as_completed(futures, timeout=deadline):
                pname = futures[fut]
                try:
                    results[pname] = fut.result(timeout=0)
                except Exception:
                    results[pname] = {"status": "error", "error": "Internal error fetching results"}
        except (TimeoutError, concurrent.futures.TimeoutError):
            pass

        # Any platform whose future didn't complete within the global deadline
        for fut, pname in futures.items():
            if pname not in results:
                if not fut.done():
                    fut.cancel()
                results[pname] = {"status": "error", "error": "Timeout"}
    finally:
        # Context-manager shutdown waits for running futures, defeating the
        # global deadline. Running thread work cannot be force-cancelled, but
        # callers must still regain control once the deadline is reached.
        executor.shutdown(wait=False, cancel_futures=True)

    return results


def _apply_post_processing(
    platform_results: dict[str, dict],
    product_platforms: bool = False,
) -> dict[str, dict]:
    """Apply deduplication and (optionally) product clustering to platform results.

    Modifies *platform_results* in place and returns it for convenience.
    """
    for p, pdata in platform_results.items():
        if pdata.get("status") != "ok":
            continue
        items = pdata.get("items", [])
        if not items:
            continue
        items = deduplicate(items)
        if product_platforms and p in ECOMMERCE_PLATFORMS:
            items = cluster_products(items)
        pdata["items"] = items
    return platform_results


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def search_all(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
    timeout: float = 30.0,
) -> dict:
    """Search ALL platforms (or whitelist) concurrently for the given keyword.

    Each platform is searched in its own thread. Single-platform failures
    are captured as independent error entries — they never cause other
    platform results to be discarded.

    Results are normalized to ContentItem or ProductItem depending on the
    platform type.

    Args:
        keyword:   Search query string.
        platforms: Platform whitelist (default: all keyword-search engines).
                   Valid values: taobao, jd, pdd, xiaohongshu, zhihu,
                                 weibo, douyin.
        limit:     Max results per platform (default 5).
        timeout:   Global timeout in seconds (default 30).

    Returns:
        {
            "keyword": str,
            "platforms": {
                "taobao": {"status": "ok", "items": [...]},
                ...
            }
        }
    """
    resolved = _resolve_platforms(platforms, ALL_PLATFORMS, ALL_PLATFORMS)
    platform_results = _run_concurrent(_search_one, resolved, keyword, limit, timeout)
    _apply_post_processing(platform_results, product_platforms=True)

    return {
        "keyword": keyword,
        "platforms": platform_results,
    }


def search_products(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """Search e-commerce platforms only (taobao/jd/pdd) concurrently.

    Returns unified ProductItem format with price comparison:
      - price_range: [min, max] across all valid prices
      - median:      median price across all valid prices

    Args:
        keyword:   Search query string.
        platforms: E-commerce platform whitelist (default: all three).
                   Valid: taobao, jd, pdd.
        limit:     Max results per platform (default 5).

    Returns:
        {
            "keyword": str,
            "platforms": {
                "taobao": {"status": "ok", "items": [...]},
                ...
            },
            "price_comparison": {
                "price_range": [min, max] | [None, None],
                "median": float | None,
            }
        }
    """
    resolved = _resolve_platforms(platforms, ECOMMERCE_PLATFORMS, ECOMMERCE_PLATFORMS)
    platform_results = _run_concurrent(_search_one, resolved, keyword, limit, timeout=30.0)

    # ── post-processing: dedup + cluster ──────────────────
    _apply_post_processing(platform_results, product_platforms=True)

    # ── price comparison ───────────────────────────────────
    all_prices: list[float] = []
    for pdata in platform_results.values():
        if pdata.get("status") != "ok":
            continue
        for item in pdata.get("items", []):
            price = item.get("price")
            if isinstance(price, (int, float)) and price is not None:
                all_prices.append(float(price))

    price_comparison: dict = {
        "price_range": [None, None],
        "median": None,
    }
    if all_prices:
        price_comparison = {
            "price_range": [min(all_prices), max(all_prices)],
            "median": round(float(statistics.median(all_prices)), 2),
        }

    return {
        "keyword": keyword,
        "platforms": platform_results,
        "price_comparison": price_comparison,
    }


def search_content(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """Search content platforms (xiaohongshu/zhihu/weibo/douyin/zsxq) concurrently.

    Returns unified ContentItem format.

    Args:
        keyword:   Search query string.
        platforms: Content platform whitelist (default: all five).
                   Valid: xiaohongshu, zhihu, weibo, douyin.
        limit:     Max results per platform (default 5).

    Returns:
        {
            "keyword": str,
            "platforms": {
                "xiaohongshu": {"status": "ok", "items": [...]},
                ...
            }
        }
    """
    resolved = _resolve_platforms(platforms, CONTENT_PLATFORMS, CONTENT_PLATFORMS)
    platform_results = _run_concurrent(_search_one, resolved, keyword, limit, timeout=30.0)
    _apply_post_processing(platform_results, product_platforms=False)

    return {
        "keyword": keyword,
        "platforms": platform_results,
    }


def get_trending(
    platforms: list[str] | None = None,
) -> dict:
    """Get aggregated trending/hot lists from all platforms that support them.

    Currently supported: weibo (热搜), zhihu (热榜), douyin (热搜).

    Returns unified TrendItem format.

    Args:
        platforms: Platform whitelist (default: all hot-list platforms).
                   Valid: weibo, zhihu, douyin.

    Returns:
        {
            "platforms": {
                "weibo": {"status": "ok", "items": [{rank, word, hot_metric, url, label}, ...]},
                ...
            }
        }
    """
    resolved = _resolve_platforms(platforms, HOTLIST_PLATFORMS, HOTLIST_PLATFORMS)
    platform_results = _run_concurrent(_hotlist_one, resolved, keyword=None, limit=0, timeout=30.0)

    return {
        "platforms": platform_results,
    }
