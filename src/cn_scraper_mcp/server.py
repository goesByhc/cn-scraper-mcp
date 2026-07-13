#!/usr/bin/env python
"""
MCP Server for Chinese web scraping — e-commerce + content platforms.

Exposes tools to AI agents (Codex, Claude Code, Cursor, Trae, Reasonix, Hermes).

Tools:
    taobao_search     — 淘宝/天猫搜索 (纯脚本, 不限流)
    jd_search         — 京东搜索 (headful Chrome + 持久登录)
    pdd_search        — 拼多多搜索 (CDP + iPhone UA，⚠️ 单次搜索限制)
    pdd_product_detail — 拼多多商品详情
    xiaohongshu_search — 小红书搜索 (本地 Chrome CDP + cookie)
    zhihu_search      — 知乎搜索 (REST API)
    zhihu_hot_list    — 知乎热榜
    weibo_search      — 微博搜索 (REST API, 需登录 cookie)
    weibo_hot_list    — 微博热搜榜 (无需登录!)
    douyin_search     — 抖音搜索 (⚠️ 实验性, 当前不可用)
    zsxq_topics       — 知识星球帖子 (REST API)
    check_cookies     — 检查所有平台 cookie 状态
    diagnose          — 环境诊断
    compare_prices    — 跨平台比价
    harvest_cookies   — 从用户浏览器通过 CDP 自动提取 cookie (含 HttpOnly)

Start:
    cn-scraper-mcp
    python -m cn_scraper_mcp.server
"""

import os
import re
import shutil
import socket
import subprocess
import sys

from fastmcp import FastMCP

from cn_scraper_mcp.errors import (  # noqa: E402
    BrowserError,
    CookieExpiredError,
    CookieMissingError,
    PlatformError,
    RateLimitError,
    ValidationError,
    error_response,
)
from cn_scraper_mcp.logging import get_logger, get_recent_errors, record_error

logger = get_logger("cn_scraper_mcp.server")

mcp = FastMCP(
    name="cn-scraper",
    instructions="""中文互联网爬虫工具 — 电商 + 内容平台全覆盖。

电商：taobao_search (纯脚本最快), jd_search (需要 Chrome), pdd_search (Chrome + ⚠️单次搜索限制)
社区：xiaohongshu_search (需要本地 Chrome), zhihu_search (REST API), weibo_search (REST API, 需登录)
热搜：weibo_hot_list (无需登录!), zhihu_hot_list (需登录)
付费社群：zsxq_topics (知识星球 API)
比价：compare_prices (跨平台价格对比)
诊断：check_cookies 看各平台 cookie 状态, diagnose 查看环境诊断""",
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


# ═══════════════════════════════════════════════════════════════
# E-commerce tools
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def taobao_search(keyword: str, limit: int = 10) -> dict:
    """搜索淘宝/天猫商品。纯脚本，无需浏览器，不限流。

    原理: curl_cffi 伪造 Chrome TLS 指纹 + MTOP HMAC-MD5 签名。
    需要 TAOBAO_COOKIES_FILE 环境变量或 ~/.cn-scraper-cookies/taobao.json。

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

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
        from cn_scraper_mcp.engines import TaobaoAPIError, TaobaoAuthError, TaobaoEngine
        engine = TaobaoEngine()
        return engine.search(keyword, limit=limit)
    except FileNotFoundError as e:
        record_error(e)
        return error_response(
            CookieMissingError(
                message="淘宝 Cookie 文件未找到",
                hint="需要淘宝 cookie 文件。放置到 ~/.cn-scraper-cookies/taobao.json "
                     "或设置 TAOBAO_COOKIES_FILE 环境变量。详见 README。",
            )
        )
    except TaobaoAuthError:
        record_error(TaobaoAuthError("淘宝登录已过期"))
        return error_response(
            CookieExpiredError(
                message="淘宝登录已过期",
                hint="在浏览器中重新登录淘宝，导出新的 cookie 文件替换旧文件。",
            )
        )
    except TaobaoAPIError:
        record_error(TaobaoAPIError("淘宝 API 返回错误"))
        return error_response(
            PlatformError(
                message="淘宝 API 返回错误",
                hint="淘宝 MTOP API 返回了异常响应，请稍后重试。",
            )
        )
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def jd_search(keyword: str, limit: int = 10) -> dict:
    """搜索京东商品。需要已登录的有头 Chrome（会自动启动）。

    京东 headless 返回 0 结果 → 必须 headful。
    需要持久登录 profile (~/.jd_login_profile)。
    首次使用需在弹窗 Chrome 中手动登录 jd.com 一次。

    并发: ⚠️ 使用 BrowserLock 保护 CDP 端口，同端口调用自动串行化。

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
        record_error(e)
        return error_response(
            BrowserError(
                message="Chrome 未找到",
                hint="请安装 Chrome 浏览器，或设置 CHROME_PATH 环境变量指向 Chrome 可执行文件。",
            )
        )
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def pdd_search(keyword: str, limit: int = 10) -> dict:
    """搜索拼多多商品。需要 Chrome + PDD cookie（PDDAccessToken + pdd_user_id）。

    ⚠️ 严重限制：拼多多手机搜索**每个浏览器会话仅允许一次搜索**。
    第一次搜索后，所有后续搜索返回「系统繁忙」。
    如需再次搜索，必须重启 MCP server 以创建新的浏览器会话。

    原理: Chrome CDP + iPhone UA 模拟手机浏览器搜索。
    Cookie 文件: ~/.cn-scraper-cookies/pdd.json
    Token 有效期约 1 小时，需定期从手机浏览器重新导出。

    并发: ⚠️ 使用 BrowserLock 保护 CDP 端口，同端口调用自动串行化。

    Args:
        keyword: 搜索关键词
        limit: 返回条数上限 (默认 10)

    Returns:
        {keyword, count, items: [{goodsId, name, price, sold, url}]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import PDDAuthError, PDDEngine, PDDRateLimitError
        engine = PDDEngine()
        return engine.search(keyword, limit=limit)
    except PDDRateLimitError as e:
        record_error(e)
        return error_response(
            RateLimitError(
                message="拼多多搜索限流 — 已达到单次搜索限制",
                hint=(
                    "拼多多手机搜索每个浏览器会话仅允许一次搜索。\n"
                    "如需再次搜索，请重启 MCP server 以创建新的浏览器会话。\n"
                    "这是拼多多服务端限制，无法绕过。"
                ),
            )
        )
    except PDDAuthError as e:
        record_error(e)
        return error_response(
            CookieExpiredError(
                message="拼多多 token 已过期",
                hint=(
                    "PDDAccessToken 有效期约 1 小时。\n"
                    "请从已登录的拼多多手机浏览器重新导出 cookie 到 "
                    "~/.cn-scraper-cookies/pdd.json"
                ),
            )
        )
    except FileNotFoundError as e:
        record_error(e)
        return error_response(
            BrowserError(
                message="Chrome 未找到",
                hint="请安装 Chrome 浏览器，或设置 CHROME_PATH 环境变量。",
            )
        )
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def compare_prices(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """跨平台比价 — 同一关键词搜索淘宝、京东、拼多多，返回价格对比。

    对同一关键词在所有请求的电商平台搜索，统一价格格式后进行对比。
    部分平台失败时不影响其他平台的结果。

    Args:
        keyword: 搜索关键词，如 "iPhone 16 Pro"
        platforms: 平台列表，默认 ["taobao", "jd"]。
                   可选: "taobao", "jd", "pdd"
        limit: 每平台返回条数 (默认 5)

    Returns:
        {
            keyword, platforms: {
                taobao: {status, items, price_range, median},
                jd: {status, items, price_range, median},
                ...
            },
            best_deal: {platform, price, title} | null
        }
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=5)

    valid_platforms = {"taobao", "jd", "pdd"}
    if platforms is None:
        platforms = ["taobao", "jd"]
    else:
        # Filter to valid platforms only
        platforms = [p for p in platforms if p in valid_platforms]
        if not platforms:
            platforms = ["taobao", "jd"]

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.compare import compare_prices as _compare
        return _compare(keyword, platforms=platforms, limit=limit)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def pdd_product_detail(url_or_id: str) -> dict:
    """获取拼多多商品详情（名称、价格、原价、销量、规格）。

    商品详情独立于搜索限制 — 不限次数。

    Args:
        url_or_id: 商品 goods_id（如 "123456789"）或完整 goods2.html URL

    Returns:
        {goodsId, name, price, origPrice, sales, specs, url, soldOut, state}
    """
    # ── input validation ────────────────────────────────
    if not isinstance(url_or_id, str) or not url_or_id.strip():
        raise ValidationError(
            "url_or_id must be a non-empty string",
            hint="Provide a PDD goods_id (e.g. '123456789') or goods2.html URL.",
        )

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import PDDAuthError, PDDEngine, PDDSoldOutError
        engine = PDDEngine()
        return engine.product_detail(url_or_id.strip())
    except PDDAuthError as e:
        record_error(e)
        return error_response(
            CookieExpiredError(
                message="拼多多 token 已过期",
                hint="PDDAccessToken 已过期，请重新导出 cookie。",
            )
        )
    except PDDSoldOutError as e:
        record_error(e)
        return error_response(
            PlatformError(
                message="商品已售罄",
                hint="该商品当前不可购买。",
            )
        )
    except FileNotFoundError as e:
        record_error(e)
        return error_response(
            BrowserError(
                message="Chrome 未找到",
                hint="请安装 Chrome 浏览器。",
            )
        )
    except Exception as e:
        record_error(e)
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

    并发: ⚠️ 使用 BrowserLock 保护 CDP 端口，同端口调用自动串行化。

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
        record_error(e)
        return error_response(
            BrowserError(
                message="Cookie 或 Chrome 未就绪",
                hint="需要小红书 cookie (~/.cn-scraper-cookies/xiaohongshu.json) "
                     "和本地 Chrome 浏览器。详见 README。",
            )
        )
    except Exception as e:
        record_error(e)
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
        record_error(e)
        return error_response(e)


@mcp.tool()
def zhihu_search(keyword: str, limit: int = 10) -> dict:
    """搜索知乎内容（问题/文章）。无登录可搜公开内容，登录后范围更广。

    无需浏览器——直接调知乎 v4 search API。
    可选 cookie: ~/.cn-scraper-cookies/zhihu.json（z_c0 + d_c0）

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

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
        record_error(e)
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
        record_error(e)
        return error_response(e)


@mcp.tool()
def weibo_search(keyword: str, limit: int = 10) -> dict:
    """搜索微博帖子。需要登录 cookies（SUB token）。

    ⚠️ 微博搜索 API 需要登录 — 游客模式不可用。
    热搜（weibo_hot_list）无需登录即可使用。

    原理: 调用 m.weibo.cn 移动端 API，解析 cards[].mblog。
    Cookie 文件: ~/.cn-scraper-cookies/weibo.json（需要 SUB cookie）

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

    Args:
        keyword: 搜索关键词，如 "华为"
        limit: 返回条数上限 (默认 10)

    Returns:
        {keyword, count, items: [{id, text, user, attitudes, comments, reposts, url}]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import WeiboEngine
        return WeiboEngine().search(keyword, limit=limit)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def weibo_hot_list() -> dict:
    """获取微博实时热搜榜。**无需登录！**

    原理: 调用 weibo.com/ajax/side/hotSearch（游客可访问）。
    返回实时热搜 50 条 + 置顶政务话题。

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

    Returns:
        {count, items: [{rank, word, num, url, label}], hotgov: {name, url}|null}
    """
    try:
        from cn_scraper_mcp.engines import WeiboEngine
        return WeiboEngine().hot_list()
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def douyin_search(keyword: str, limit: int = 10) -> dict:
    """搜索抖音视频/内容 — ⚠️ 当前不可用（需签名请求）。

    抖音 API 需要加密签名（X-Gorgon / X-Khronos / X-Argus），
    签名算法使用混淆 native 代码且频繁更新，目前无法绕过。

    本工具返回诚实的错误信息和替代方案（第三方数据服务、开放平台等）。
    如果将来发现可用的游客端点，将实现实际搜索功能。

    Args:
        keyword: 搜索关键词
        limit: 返回条数上限 (接受但忽略)

    Returns:
        {keyword, error, status: "UNSUPPORTED", alternatives: [...]}
    """
    # ── input validation (BEFORE any network call) ─────
    keyword = _validate_keyword(keyword)
    limit = _validate_limit(limit, default=10)

    # ── execution ───────────────────────────────────────
    try:
        from cn_scraper_mcp.engines import DouyinEngine
        return DouyinEngine().search(keyword, limit=limit)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def zsxq_topics(group_id: str, count: int = 5, owner_only: bool = False) -> dict:
    """获取知识星球 (ZSXQ) 付费社群最新帖子。

    纯 REST API，无需浏览器，只需 cookie。
    Cookie 文件: ~/.cn-scraper-cookies/zsxq.json (需要 zsxq_access_token)

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

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
        record_error(e)
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Cookie harvest — CDP-based auto-extraction from user's browser
# ═══════════════════════════════════════════════════════════════

_VALID_HARVEST_PLATFORMS = {"taobao", "xiaohongshu", "zhihu", "zsxq", "jd", "pdd"}


def _validate_platform(platform: str) -> str:
    """Validate and clean a platform name for harvest_cookies. Raises ValidationError."""
    if not isinstance(platform, str):
        raise ValidationError(
            f"platform must be a string, got {type(platform).__name__}",
            hint=f"Pass one of: {', '.join(sorted(_VALID_HARVEST_PLATFORMS))}",
        )
    cleaned = platform.strip().lower()
    if not cleaned:
        raise ValidationError(
            "platform must not be empty",
            hint=f"Pass one of: {', '.join(sorted(_VALID_HARVEST_PLATFORMS))}",
        )
    if cleaned not in _VALID_HARVEST_PLATFORMS:
        raise ValidationError(
            f"Unsupported platform '{cleaned}'",
            hint=f"Supported platforms: {', '.join(sorted(_VALID_HARVEST_PLATFORMS))}",
        )
    return cleaned


@mcp.tool()
def harvest_cookies(platform: str, port: int | None = None) -> dict:
    """从用户自己的浏览器会话中自动提取 cookie（包括 HttpOnly cookie）。

    通过 Chrome DevTools Protocol (CDP) 的 Network.getAllCookies 提取
    浏览器 cookie jar 中的所有 cookie（含 HttpOnly，这是 JS 无法获取的）。
    仅提取用户**自己**的浏览器会话——浏览器须已在指定端口运行且已登录。

    提取的 cookie 保存到 ~/.cn-scraper-cookies/<platform>.json。

    Args:
        platform: 平台名 — 'taobao', 'xiaohongshu', 'zhihu', 'zsxq', 'jd', 'pdd'
        port:     CDP 调试端口 (可选, 各平台有默认值)

    Returns:
        {platform, count, saved_to, status}
    """
    # ── input validation ──────────────────────────────────
    platform = _validate_platform(platform)
    if port is not None and (not isinstance(port, int) or port < 1024 or port > 65535):
        raise ValidationError(
            f"port must be between 1024 and 65535, got {port}",
            hint="Provide a valid CDP debug port number.",
        )

    # ── execution ─────────────────────────────────────────
    try:
        from cn_scraper_mcp.cookie_harvest import CookieHarvestError, CookieHarvester
        harvester = CookieHarvester()
        return harvester.harvest(platform, port=port)
    except CookieHarvestError as e:
        record_error(e)
        return error_response(
            BrowserError(
                message=str(e),
                hint="请确保 Chrome 已使用 --remote-debugging-port 启动，且有打开的标签页。",
            )
        )
    except ValueError as e:
        record_error(e)
        return error_response(
            ValidationError(
                message=str(e),
                hint=f"Supported platforms: {', '.join(sorted(_VALID_HARVEST_PLATFORMS))}",
            )
        )
    except Exception as e:
        record_error(e)
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Diagnostics
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def check_cookies() -> dict:
    """检查所有平台的 cookie 文件是否存在、有效字段及新鲜度。

    Cookie 文件查找路径 (按优先级):
      1. 平台专用环境变量 (如 TAOBAO_COOKIES_FILE)
      2. ~/.cn-scraper-cookies/<name>.json (推荐)
    JD 特殊: 检查 Chrome profile 目录 ~/.jd_login_profile

    Returns:
        {taobao, xiaohongshu, zhihu, zsxq, jd, pdd:
            {exists, valid, missing_fields, path, age_hours, stale}}
    """
    from cn_scraper_mcp.auth import check_all_cookies
    return check_all_cookies()


@mcp.tool()
def diagnose() -> dict:
    """诊断平台环境 — 检查 Python、依赖、Chrome、CDP 端口、Cookie、最近错误。

    不做任何实际抓取，纯本地诊断。每项检查超时 5 秒。

    Returns:
        sections:
          platform:      {package_version, python_version}
          dependencies:  {fastmcp: {installed, version}, curl_cffi: {...}, ...}
          browsers:      {chrome: {found, path, version}, obscura: {found, path}}
          cdp_ports:     {9222: {in_use}, 9247: {...}, 9251: {...}}
          cookies:       来自 check_all_cookies() 的结果
          diagnostics:   {recent_errors: [...]}
    """
    import platform

    from cn_scraper_mcp import __version__

    result = {
        "platform": {},
        "dependencies": {},
        "browsers": {},
        "cdp_ports": {},
        "cookies": {},
        "diagnostics": {},
    }

    # ── platform ────────────────────────────────────────────
    result["platform"] = {
        "package_version": __version__,
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
    }

    # ── dependencies ────────────────────────────────────────
    deps_to_check = ["fastmcp", "curl_cffi", "websockets", "dotenv"]
    for dep_name in deps_to_check:
        result["dependencies"][dep_name] = _check_dependency(dep_name)

    # ── browsers ────────────────────────────────────────────
    result["browsers"]["chrome"] = _check_chrome()
    result["browsers"]["obscura"] = _check_obscura()

    # ── CDP ports ───────────────────────────────────────────
    for port in (9222, 9247, 9251):
        result["cdp_ports"][str(port)] = _check_port(port)

    # ── cookies ─────────────────────────────────────────────
    try:
        from cn_scraper_mcp.auth import check_all_cookies
        result["cookies"] = check_all_cookies()
    except Exception as e:
        result["cookies"] = {"error": str(e)}

    # ── recent errors ───────────────────────────────────────
    result["diagnostics"]["recent_errors"] = get_recent_errors()

    return result


# ═══════════════════════════════════════════════════════════════
# Diagnose helpers
# ═══════════════════════════════════════════════════════════════

_DIAGNOSE_TIMEOUT = 5


def _check_dependency(name: str) -> dict:
    """Check if a Python package is installed and get its version."""
    try:
        mod = __import__(name)
        version = getattr(mod, "__version__", "unknown")
        return {"installed": True, "version": version}
    except ImportError:
        return {"installed": False, "version": None}
    except Exception as e:
        return {"installed": False, "version": None, "error": str(e)[:100]}


def _check_chrome() -> dict:
    """Check if Chrome is installed and get its version."""
    result: dict = {"found": False, "path": None, "version": None}

    # Check CHROME_PATH env var first
    chrome_path = os.environ.get("CHROME_PATH")
    if chrome_path and os.path.exists(chrome_path):
        result["found"] = True
        result["path"] = chrome_path
    else:
        # Search common locations
        candidates = []
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                shutil.which("chrome"),
            ]
        elif sys.platform == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                shutil.which("google-chrome"),
                shutil.which("chrome"),
            ]
        else:
            candidates = [
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                shutil.which("chrome"),
            ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                result["found"] = True
                result["path"] = candidate
                break
        else:
            # Not found in candidates — try shutil.which("chrome") as fallback
            fallback = shutil.which("chrome")
            if fallback:
                result["found"] = True
                result["path"] = fallback

    # Get version
    if result["found"] and result["path"]:
        try:
            proc = subprocess.run(
                [result["path"], "--version"],
                capture_output=True, text=True,
                timeout=_DIAGNOSE_TIMEOUT,
            )
            result["version"] = proc.stdout.strip() or proc.stderr.strip()
        except (subprocess.TimeoutExpired, Exception):
            result["version"] = "timeout"
    else:
        result["version"] = None

    return result


def _check_obscura() -> dict:
    """Check if Obscura is installed."""
    result: dict = {"found": False, "path": None}
    obscura_path = shutil.which("obscura")
    if obscura_path:
        result["found"] = True
        result["path"] = obscura_path
    return result


def _check_port(port: int) -> dict:
    """Check if a TCP port is in use (listening)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(_DIAGNOSE_TIMEOUT)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return {"in_use": result == 0}
    except (TimeoutError, OSError, Exception):
        return {"in_use": False, "error": "timeout"}


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    """Entry point for `cn-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
