"""Cross-platform price comparison for e-commerce platforms.

Compares prices for the same keyword across Taobao, JD, and PDD.
Handles partial failures gracefully — one platform failing doesn't
block the others.
"""

from __future__ import annotations

import importlib
import statistics
from typing import Any

from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.compare")

# Maps platform names to their engine classes + search config
_PLATFORM_REGISTRY: dict[str, dict[str, Any]] = {
    "taobao": {
        "module": "cn_scraper_mcp.engines.taobao",
        "class": "TaobaoEngine",
        "item_name_field": "title",
        "item_price_field": "price",
        "item_url_field": "url",
    },
    "jd": {
        "module": "cn_scraper_mcp.engines.jd",
        "class": "JDEngine",
        "item_name_field": "name",
        "item_price_field": "price",
        "item_url_field": "url",
    },
    "pdd": {
        "module": "cn_scraper_mcp.engines.pdd",
        "class": "PDDEngine",
        "item_name_field": "name",
        "item_price_field": "price",
        "item_url_field": "url",
    },
}


def _normalize_price(price: Any) -> float | None:
    """Convert a price value to a float, handling strings like '¥6,999.00'.

    Returns None if the price cannot be parsed.
    """
    if price is None:
        return None
    if isinstance(price, (int, float)):
        return float(price)
    # String: strip ¥, commas, spaces, and convert
    cleaned = str(price).strip()
    # Remove currency symbols and whitespace
    for char in ("¥", "￥", "$", "元", " "):
        cleaned = cleaned.replace(char, "")
    # Remove commas inside numbers
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def _extract_prices(items: list[dict], price_field: str) -> list[float]:
    """Extract normalized prices from a list of items."""
    prices = []
    for item in items:
        p = _normalize_price(item.get(price_field))
        if p is not None:
            prices.append(p)
    return prices


def _compute_platform_summary(
    items: list[dict],
    price_field: str,
) -> dict:
    """Compute price_range and median for a platform's items."""
    prices = _extract_prices(items, price_field)
    if not prices:
        return {
            "price_range": [None, None],
            "median": None,
        }
    return {
        "price_range": [min(prices), max(prices)],
        "median": round(float(statistics.median(prices)), 2),
    }


def _search_one_platform(
    platform: str,
    keyword: str,
    limit: int,
) -> dict:
    """Search a single platform and return structured result.

    Never raises — always returns a dict with status and items/error.
    """
    config = _PLATFORM_REGISTRY.get(platform)
    if config is None:
        return {
            "status": "error",
            "error": f"Unknown platform: {platform}",
            "items": [],
        }

    try:
        mod = importlib.import_module(config["module"])

        engine_cls = getattr(mod, config["class"])
        engine = engine_cls()
        result = engine.search(keyword, limit=limit)

        # Check if the engine returned an error dict (e.g. JD browser failure)
        if isinstance(result, dict) and "error" in result:
            return {
                "status": "error",
                "error": result.get("error", "Unknown platform error"),
                "items": [],
            }

        items = result.get("items", [])
        summary = _compute_platform_summary(items, config["item_price_field"])

        return {
            "status": "ok",
            "items": items,
            **summary,
        }

    except Exception as e:
        logger.warning("Platform '%s' search failed for keyword '%s': %s", platform, keyword, e)
        return {
            "status": "error",
            "error": str(e),
            "items": [],
        }


def _find_best_deal(
    platforms_data: dict,
    platform_configs: dict[str, dict],
) -> dict | None:
    """Find the cheapest item across all successful platforms.

    Returns {platform, price, title} or None if no items found.
    """
    candidates = []
    for pname, pdata in platforms_data.items():
        if pdata.get("status") != "ok":
            continue
        config = platform_configs.get(pname)
        if config is None:
            continue
        items = pdata.get("items", [])
        price_field = config["item_price_field"]
        name_field = config["item_name_field"]
        for item in items:
            price = _normalize_price(item.get(price_field))
            title = item.get(name_field, "")
            if price is not None:
                candidates.append({
                    "platform": pname,
                    "price": price,
                    "title": title,
                })

    if not candidates:
        return None

    # Find the one with the lowest price
    best = min(candidates, key=lambda c: c["price"])
    return best


def compare_prices(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """Cross-platform price comparison for e-commerce products.

    Searches multiple platforms for the same keyword and returns
    a structured comparison including price ranges, medians, and
    the best deal across all platforms.

    Partial failures are handled gracefully — if one platform
    fails, the others still return their results.

    Args:
        keyword: Search keyword (e.g. "iPhone 16 Pro")
        platforms: List of platform names. Default: ["taobao", "jd"].
                   Supported: "taobao", "jd", "pdd".
        limit: Max items per platform (default 5).

    Returns:
        {
            "keyword": "...",
            "platforms": {
                "taobao": {
                    "status": "ok",
                    "items": [...],
                    "price_range": [min, max],
                    "median": ...
                },
                "jd": { ... }
            },
            "best_deal": {"platform": "taobao", "price": 3099.0, "title": "..."}
        }
    """
    if platforms is None:
        platforms = ["taobao", "jd"]

    # Deduplicate while preserving order
    seen = set()
    unique_platforms = []
    for p in platforms:
        if p not in seen:
            seen.add(p)
            unique_platforms.append(p)
    platforms = unique_platforms

    platforms_data: dict[str, dict] = {}

    for pname in platforms:
        platforms_data[pname] = _search_one_platform(pname, keyword, limit)

    best_deal = _find_best_deal(platforms_data, _PLATFORM_REGISTRY)

    return {
        "keyword": keyword,
        "platforms": platforms_data,
        "best_deal": best_deal,
    }
