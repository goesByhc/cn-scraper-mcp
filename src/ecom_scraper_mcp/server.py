#!/usr/bin/env python
"""
MCP Server for Chinese e-commerce scraping.

Exposes tools to AI agents (Codex, Claude Code, Cursor, Trae, Reasonix, Hermes).

Tools:
    taobao_search  — Search Taobao/Tmall (pure script, no browser, no rate limit)
    jd_search      — Search JD.com (requires headful Chrome with login)
    check_cookies  — Check cookie freshness for all platforms

Start:
    ecom-scraper-mcp          # if installed via pip
    python -m ecom_scraper_mcp.server
"""

import json, os, sys, datetime
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP(
    name="ecom-scraper",
    instructions="""中文电商跨平台比价工具。支持淘宝、京东搜索。

- taobao_search: 纯脚本，最快最稳，适合批量比价
- jd_search: 需要已登录的有头 Chrome（会自动尝试启动）
- 搜索前先用 check_cookies 看各平台 cookie 状态
""",
)


# ─── helpers ───────────────────────────────────────────────

def _cookie_status(platform: str, filename: str) -> dict:
    """Check existence and freshness of a cookie file."""
    p = Path.home() / ".ecom-cookies" / filename
    # also check legacy paths
    alt = Path.home() / "jd_scrape" / filename
    for path in (p, alt):
        if path.exists():
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
            return {
                "exists": True,
                "path": str(path),
                "mtime": mtime.isoformat(),
                "age_hours": round(age_h, 1),
                "stale": age_h > 72,
            }
    return {"exists": False}


# ─── MCP Tools ─────────────────────────────────────────────

@mcp.tool()
def taobao_search(keyword: str, limit: int = 10) -> dict:
    """搜索淘宝/天猫商品。纯脚本，无需浏览器，不限流。

    原理: curl_cffi 伪造 Chrome TLS 指纹 + MTOP HMAC-MD5 签名。
    需要配置 TAOBAO_COOKIES_FILE 环境变量或 ~/.ecom-cookies/taobao.json。

    Args:
        keyword: 搜索关键词，如 "华为mate70", "儿童学习桌"
        limit: 返回条数上限 (默认 10)

    Returns:
        {"keyword": str, "total": int, "items": [{title, price, origPrice, sales, id, shop, url}]}
    """
    from ecom_scraper_mcp.engines import TaobaoEngine, TaobaoAuthError

    try:
        engine = TaobaoEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return {"error": "Cookie 文件未找到", "detail": str(e),
                "hint": "需要从已登录的淘宝浏览器导出 cookie 为 JSON 文件。详见 README。"}
    except TaobaoAuthError as e:
        return {"error": "淘宝登录过期", "detail": str(e),
                "hint": "需要刷新 cookie。运行 refresh_taobao_cookies 或重新登录。"}
    except Exception as e:
        return {"error": f"淘宝搜索失败: {e}"}


@mcp.tool()
def jd_search(keyword: str, limit: int = 10) -> dict:
    """搜索京东商品。需要已登录的有头 Chrome（会自动启动）。

    京东对无头/无 cookie 风控极严：
    - 必须 headful (有头浏览器)
    - 需要持久登录 profile (~/.jd_login_profile)
    - 首次使用需在弹窗的 Chrome 中手动登录 jd.com 一次，之后记住登录态

    Args:
        keyword: 搜索关键词
        limit: 返回条数上限 (默认 10)

    Returns:
        {"keyword": str, "count": int, "items": [{sku, name, price, ad, url}]}
    """
    try:
        from ecom_scraper_mcp.engines import JDEngine
        engine = JDEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return {"error": "Chrome 未找到", "detail": str(e),
                "hint": "需要安装 Chrome 浏览器。Chrome 路径可通过 CHROME_PATH 环境变量指定。"}
    except Exception as e:
        return {"error": f"京东搜索失败: {e}"}


@mcp.tool()
def check_cookies() -> dict:
    """检查各平台 cookie 文件是否存在及新鲜度。

    Cookie 文件查找路径:
      1. ~/.ecom-cookies/<platform>.json (推荐)
      2. ~/jd_scrape/<filename>.json (旧路径兼容)

    Returns:
        {taobao: {exists, age_hours, stale}, jd: {...}, pdd: {...}}
    """
    return {
        "taobao": _cookie_status("taobao", "taobao.json"),
        "jd": _cookie_status("jd", "cookies_full.json"),
        "pdd": _cookie_status("pdd", "pdd_cookies.json"),
    }


# ─── entry point ────────────────────────────────────────────

def main():
    """Entry point for `ecom-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
