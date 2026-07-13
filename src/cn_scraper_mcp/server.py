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

import json, os, sys, datetime, re
from pathlib import Path

from fastmcp import FastMCP

from cn_scraper_mcp.errors import (  # noqa: E402
    ScraperError,
    CookieExpiredError,
    CookieMissingError,
    AuthRequiredError,
    RateLimitError,
    ParseError,
    BrowserError,
    ValidationError,
    PlatformError,
    error_response,
)

mcp = FastMCP(
    name="cn-scraper",
    instructions="""中文互联网爬虫工具 — 电商 + 内容平台全覆盖。

电商：taobao_search (纯脚本最快), jd_search (需要 Chrome)
社区：xiaohongshu_search (需要本地 Chrome), zhihu_search (REST API)
付费社群：zsxq_topics (知识星球 API)
诊断：check_cookies 看各平台 cookie 状态""",
)


# ═══════════════════════════════════════════════════════════════
# Input validators — called at the TOP of every tool function
# ═══════════════════════════════════════════════════════════════

_KEYWORD_MAX_LEN = 200
_LIMIT_MIN = 1
_LIMIT_MAX = 50
_COUNT_MIN = 1
_COUNT_MAX = 20

_ALPHANUMERIC_RE = re.compile(r"^[a-zA-Z0-9]+$")


def _validate_keyword(keyword: str) -> str:
    """Validate and clean a search keyword. Raises ValidationError on bad input."""
    if not isinstance(keyword, str):
        raise ValidationError(
            f"keyword must be a string, got {type(keyword).__name__}",
            hint="Pass a non-empty string for the keyword parameter.",
        )
    cleaned = keyword.strip()
    if not cleaned:
        raise ValidationError(
            "keyword must not be empty",
            hint="Provide a non-empty search keyword (e.g. '华为mate70').",
        )
    if len(cleaned) > _KEYWORD_MAX_LEN:
        raise ValidationError(
            f"keyword must be at most {_KEYWORD_MAX_LEN} characters, got {len(cleaned)}",
            hint=f"Shorten the keyword to {_KEYWORD_MAX_LEN} characters or fewer.",
        )
    return cleaned


def _validate_limit(limit: int, default: int = 10) -> int:
    """Clamp limit to [_LIMIT_MIN, _LIMIT_MAX]. Never raises — always returns a safe value."""
    if not isinstance(limit, int):
        limit = default
    return max(_LIMIT_MIN, min(_LIMIT_MAX, limit))


def _validate_count(count: int, default: int = 5) -> int:
    """Clamp count to [_COUNT_MIN, _COUNT_MAX]. Never raises — always returns a safe value."""
    if not isinstance(count, int):
        count = default
    return max(_COUNT_MIN, min(_COUNT_MAX, count))


def _validate_group_id(group_id: str) -> str:
    """Validate group_id: non-empty and numeric. Raises ValidationError."""
    if not isinstance(group_id, str):
        raise ValidationError(
            f"group_id must be a string, got {type(group_id).__name__}",
            hint="Pass a numeric group ID as a string (e.g. '28888555451').",
        )
    cleaned = group_id.strip()
    if not cleaned:
        raise ValidationError(
            "group_id must not be empty",
            hint="Provide the numeric ZSXQ group/planet ID.",
        )
    if not cleaned.isdigit():
        raise ValidationError(
            f"group_id must be numeric, got '{cleaned}'",
            hint="The ZSXQ group ID should be all digits (e.g. '28888555451').",
        )
    return cleaned


def _validate_note_id(note_id: str) -> str:
    """Validate note_id: non-empty and alphanumeric. Raises ValidationError."""
    if not isinstance(note_id, str):
        raise ValidationError(
            f"note_id must be a string, got {type(note_id).__name__}",
            hint="Pass the note ID string from xiaohongshu_search results.",
        )
    cleaned = note_id.strip()
    if not cleaned:
        raise ValidationError(
            "note_id must not be empty",
            hint="Provide a valid Xiaohongshu note ID.",
        )
    if not _ALPHANUMERIC_RE.match(cleaned):
        raise ValidationError(
            f"note_id must be alphanumeric, got '{cleaned}'",
            hint="The note ID should contain only letters and digits.",
        )
    return cleaned


# ─── helpers ───────────────────────────────────────────────

def _cookie_status(platform: str, filename: str) -> dict:
    """Check existence and freshness of a cookie file."""
    p = Path.home() / ".cn-scraper-cookies" / filename
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


# ═══════════════════════════════════════════════════════════════
# E-commerce tools
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def taobao_search(keyword: str, limit: int = 10) -> dict:
    """搜索淘宝/天猫商品。纯脚本，无需浏览器，不限流。

    原理: curl_cffi 伪造 Chrome TLS 指纹 + MTOP HMAC-MD5 签名。
    需要 TAOBAO_COOKIES_FILE 环境变量或 ~/.cn-scraper-cookies/taobao.json。

    Args:
        keyword: 搜索关键词，如 "华为mate70"
        limit: 返回条数上限 (默认 10)

    Returns:
        {keyword, total, items: [{title, price, origPrice, sales, id, shop, url}]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import TaobaoEngine, TaobaoAuthError, TaobaoAPIError
        engine = TaobaoEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return error_response(
            CookieMissingError(
                message="淘宝 Cookie 文件未找到",
                hint="需要淘宝 cookie 文件。放置到 ~/.cn-scraper-cookies/taobao.json "
                     "或设置 TAOBAO_COOKIES_FILE 环境变量。详见 README。",
            )
        )
    except TaobaoAuthError:
        return error_response(
            CookieExpiredError(
                message="淘宝登录已过期",
                hint="在浏览器中重新登录淘宝，导出新的 cookie 文件替换旧文件。",
            )
        )
    except TaobaoAPIError:
        return error_response(
            PlatformError(
                message="淘宝 API 返回错误",
                hint="淘宝 MTOP API 返回了异常响应，请稍后重试。",
            )
        )
    except Exception as e:
        return error_response(e)


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
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import JDEngine
        return JDEngine().search(keyword, limit=limit)
    except FileNotFoundError as e:
        return error_response(
            BrowserError(
                message="Chrome 未找到",
                hint="请安装 Chrome 浏览器，或设置 CHROME_PATH 环境变量指向 Chrome 可执行文件。",
            )
        )
    except Exception as e:
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Content / social platform tools
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def xiaohongshu_search(keyword: str, limit: int = 10) -> dict:
    """搜索小红书笔记。需要本地 Chrome + XHS 登录 cookie。

    小红书只允许住宅 IP（本地 Chrome）——云浏览器/数据中心 IP 直接封。
    需要 XHS cookies: web_session, a1, webId, gid 等。
    Cookie 文件: ~/.cn-scraper-cookies/xiaohongshu.json

    Args:
        keyword: 搜索关键词
        limit: 返回条数 (默认 10)

    Returns:
        {keyword, items: [{title, author, likes, noteId, href}]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        return error_response(
            BrowserError(
                message="Cookie 或 Chrome 未就绪",
                hint="需要小红书 cookie (~/.cn-scraper-cookies/xiaohongshu.json) "
                     "和本地 Chrome 浏览器。详见 README。",
            )
        )
    except Exception as e:
        return error_response(e)


@mcp.tool()
def xiaohongshu_note(note_id: str) -> dict:
    """获取小红书笔记详情（标题、正文、点赞、标签、评论）。

    Args:
        note_id: 笔记 ID（从 xiaohongshu_search 结果中的 noteId 字段）

    Returns:
        {id, title, desc, likes, collects, comments, tags, user, time}
    """
    # ── input validation (BEFORE any network call) ─────
    note_id = _validate_note_id(note_id)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        return engine.get_note(note_id)
    except Exception as e:
        return error_response(e)


@mcp.tool()
def zhihu_search(keyword: str, limit: int = 10) -> dict:
    """搜索知乎内容（问题/文章）。无登录可搜公开内容，登录后范围更广。

    无需浏览器——直接调知乎 v4 search API。
    可选 cookie: ~/.cn-scraper-cookies/zhihu.json（z_c0 + d_c0）

    Args:
        keyword: 搜索关键词
        limit: 返回条数 (默认 10)

    Returns:
        {keyword, items: [{title, excerpt, url, type, votes, comments}]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import ZhihuEngine
        return ZhihuEngine().search(keyword, limit=limit)
    except Exception as e:
        return error_response(e)


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
        return error_response(e)


@mcp.tool()
def zsxq_topics(group_id: str, count: int = 5, owner_only: bool = False) -> dict:
    """获取知识星球 (ZSXQ) 付费社群最新帖子。

    纯 REST API，无需浏览器，只需 cookie。
    Cookie 文件: ~/.cn-scraper-cookies/zsxq.json (需要 zsxq_access_token)

    Args:
        group_id: 星球 ID (数字，如 "28888555451")
        count: 获取帖子数量 (默认 5)
        owner_only: 只看星主帖子 (默认 False)

    Returns:
        {group_id, count, topics: [{topic_id, title, text, author, created_at, comments}]}
    """
    # ── input validation (BEFORE any network call) ─────
    group_id = _validate_group_id(group_id)
    count = _validate_count(count, default=5)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import ZsxqEngine
        return ZsxqEngine().get_topics(group_id, count=count, owner_only=owner_only)
    except Exception as e:
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Diagnostics
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def check_cookies() -> dict:
    """检查所有平台的 cookie 文件是否存在及新鲜度。

    文件查找路径 (按优先级):
      1. ~/.cn-scraper-cookies/<name>.json (推荐)
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


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    """Entry point for `cn-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
