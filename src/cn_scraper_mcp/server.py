#!/usr/bin/env python
"""
MCP Server for Chinese web scraping — e-commerce + content platforms.

Exposes tools to AI agents (Codex, Claude Code, Cursor, Trae, Reasonix, Hermes).

Tools:
    taobao_search     — 淘宝/天猫搜索 (纯脚本, 不限流)
    jd_search         — 京东搜索 (headful Chrome + 持久登录)
    xiaohongshu_search — 小红书搜索 (本地 Chrome CDP + cookie)
    zhihu_search      — 知乎搜索 (REST API)
    zhihu_hot_list    — 知乎热榜
    zsxq_topics       — 知识星球帖子 (REST API)
    check_cookies     — 检查所有平台 cookie 状态

Start:
    cn-scraper-mcp
    python -m cn_scraper_mcp.server
"""

import json, os, sys, datetime
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP(
    name="cn-scraper",
    instructions="""中文互联网爬虫工具 — 电商 + 内容平台全覆盖。

电商：taobao_search (纯脚本最快), jd_search (需要 Chrome)
社区：xiaohongshu_search (需要本地 Chrome), zhihu_search (REST API)
付费社群：zsxq_topics (知识星球 API)
诊断：check_cookies 看各平台 cookie 状态""",
)


# ─── helpers ───────────────────────────────────────────────

def _cookie_status(platform: str, filename: str) -> dict:
    """Check existence and freshness of a cookie file."""
    p = Path.home() / ".ecom-cookies" / filename
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


# ─── E-commerce tools ──────────────────────────────────────

@mcp.tool()
def taobao_search(keyword: str, limit: int = 10) -> dict:
    """搜索淘宝/天猫商品。纯脚本，无需浏览器，不限流。

    原理: curl_cffi 伪造 Chrome TLS 指纹 + MTOP HMAC-MD5 签名。
    需要 TAOBAO_COOKIES_FILE 环境变量或 ~/.ecom-cookies/taobao.json。

    Args:
        keyword: 搜索关键词，如 "华为mate70"
        limit: 返回条数上限 (默认 10)

    Returns:
        {keyword, total, items: [{title, price, origPrice, sales, id, shop, url}]}
    """
    from cn_scraper_mcp.engines import TaobaoEngine, TaobaoAuthError
    try:
        engine = TaobaoEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return {"error": "Cookie 文件未找到", "detail": str(e),
                "hint": "需要淘宝 cookie。详见 README。"}
    except TaobaoAuthError as e:
        return {"error": "淘宝登录过期", "detail": str(e)}
    except Exception as e:
        return {"error": f"淘宝搜索失败: {e}"}


@mcp.tool()
def jd_search(keyword: str, limit: int = 10) -> dict:
    """搜索京东商品。需要已登录的有头 Chrome（会自动启动）。

    京东 headless 返回 0 结果 → 必须 headful。
    需要持久登录 profile (~/.jd_login_profile)。
    首次使用需在弹窗 Chrome 中手动登录 jd.com 一次。

    Args:
        keyword: 搜索关键词
        limit: 返回条数上限 (默认 10)

    Returns:
        {keyword, count, items: [{sku, name, price, ad, url}]}
    """
    try:
        from cn_scraper_mcp.engines import JDEngine
        return JDEngine().search(keyword, limit=limit)
    except FileNotFoundError as e:
        return {"error": "Chrome 未找到", "detail": str(e)}
    except Exception as e:
        return {"error": f"京东搜索失败: {e}"}


# ─── Content / social platform tools ────────────────────────

@mcp.tool()
def xiaohongshu_search(keyword: str, limit: int = 10) -> dict:
    """搜索小红书笔记。需要本地 Chrome + XHS 登录 cookie。

    小红书只允许住宅 IP（本地 Chrome）——云浏览器/数据中心 IP 直接封。
    需要 XHS cookies: web_session, a1, webId, gid 等。
    Cookie 文件: ~/.ecom-cookies/xiaohongshu.json

    Args:
        keyword: 搜索关键词
        limit: 返回条数 (默认 10)

    Returns:
        {keyword, items: [{title, author, likes, noteId, href}]}
    """
    try:
        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return {"error": "Cookie 或 Chrome 未就绪", "detail": str(e)}
    except Exception as e:
        return {"error": f"小红书搜索失败: {e}"}


@mcp.tool()
def xiaohongshu_note(note_id: str) -> dict:
    """获取小红书笔记详情（标题、正文、点赞、标签、评论）。

    Args:
        note_id: 笔记 ID（从 xiaohongshu_search 结果中的 noteId 字段）

    Returns:
        {id, title, desc, likes, collects, comments, tags, user, time}
    """
    try:
        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        return engine.get_note(note_id)
    except Exception as e:
        return {"error": str(e), "noteId": note_id}


@mcp.tool()
def zhihu_search(keyword: str, limit: int = 10) -> dict:
    """搜索知乎内容（问题/文章）。无登录可搜公开内容，登录后范围更广。

    无需浏览器——直接调知乎 v4 search API。
    可选 cookie: ~/.ecom-cookies/zhihu.json（z_c0 + d_c0）

    Args:
        keyword: 搜索关键词
        limit: 返回条数 (默认 10)

    Returns:
        {keyword, items: [{title, excerpt, url, type, votes, comments}]}
    """
    try:
        from cn_scraper_mcp.engines import ZhihuEngine
        return ZhihuEngine().search(keyword, limit=limit)
    except Exception as e:
        return {"error": f"知乎搜索失败: {e}"}


@mcp.tool()
def zhihu_hot_list() -> dict:
    """获取知乎实时热榜。无需登录。

    Returns:
        {items: [{title, url, excerpt, hot_metric}]}
    """
    try:
        from cn_scraper_mcp.engines import ZhihuEngine
        return ZhihuEngine().hot_list()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def zsxq_topics(group_id: str, count: int = 5, owner_only: bool = False) -> dict:
    """获取知识星球 (ZSXQ) 付费社群最新帖子。

    纯 REST API，无需浏览器，只需 cookie。
    Cookie 文件: ~/.ecom-cookies/zsxq.json (需要 zsxq_access_token)

    Args:
        group_id: 星球 ID (数字，如 "28888555451")
        count: 获取帖子数量 (默认 5)
        owner_only: 只看星主帖子 (默认 False)

    Returns:
        {group_id, count, topics: [{topic_id, title, text, author, created_at, comments}]}
    """
    try:
        from cn_scraper_mcp.engines import ZsxqEngine
        return ZsxqEngine().get_topics(group_id, count=count, owner_only=owner_only)
    except Exception as e:
        return {"error": f"知识星球抓取失败: {e}"}


# ─── diagnostics ────────────────────────────────────────────

@mcp.tool()
def check_cookies() -> dict:
    """检查所有平台的 cookie 文件是否存在及新鲜度。

    文件查找路径 (按优先级):
      1. ~/.ecom-cookies/<name>.json (推荐)
      2. ~/jd_scrape/<name>.json (旧路径兼容)

    Returns:
        {taobao, jd, pdd, xiaohongshu, zhihu, zsxq: {exists, age_hours, stale}}
    """
    return {
        "taobao":       _cookie_status("淘宝",   "taobao.json"),
        "jd":           _cookie_status("京东",   "cookies_full.json"),
        "pdd":          _cookie_status("拼多多", "pdd_cookies.json"),
        "xiaohongshu":  _cookie_status("小红书", "xiaohongshu.json"),
        "zhihu":        _cookie_status("知乎",   "zhihu.json"),
        "zsxq":         _cookie_status("知识星球","zsxq.json"),
    }


# ─── entry point ────────────────────────────────────────────

def main():
    """Entry point for `cn-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
