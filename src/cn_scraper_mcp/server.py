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
    xiaohongshu_note  — 小红书笔记详情
    zhihu_search      — 知乎搜索 (REST API)
    zhihu_hot_list    — 知乎热榜
    weibo_search      — 微博搜索 (REST API, 需登录 cookie)
    weibo_hot_list    — 微博热搜榜 (无需登录!)
    weibo_user_timeline — 微博用户时间线
    douyin_search     — 抖音搜索 (⚠️ 实验性, CDP)
    douyin_hot_list   — 抖音热搜榜
    zsxq_topics       — 知识星球帖子 (REST API)
    check_cookies     — 检查所有平台 cookie 状态
    diagnose          — 环境诊断
    compare_prices    — 跨平台比价
    harvest_cookies   — 从用户浏览器通过 CDP 自动提取 cookie (含 HttpOnly)
    guided_login      — 引导式登录 + 自动收割 Cookie
    search_all        — 跨平台全量搜索（7 个关键词搜索平台并发）
    search_products   — 跨平台电商搜索（淘宝/京东/拼多多）
    search_content    — 跨平台内容搜索（小红书/知乎/微博/抖音/知识星球）
    get_trending      — 跨平台热搜聚合（微博/知乎/抖音）

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

from cn_scraper_mcp.errors import (
    BrowserUnavailableError,
    CaptchaRequiredError,
    CDPUnavailableError,
    CookieMissingError,
    PlatformError,
    RateLimitError,
    RiskControlledError,
    SessionExpiredError,
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
聚合：search_all (全平台), search_products (电商), search_content (内容), get_trending (热搜)
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

_VALID_ECOMMERCE = frozenset({"taobao", "jd", "pdd"})
_VALID_CONTENT = frozenset({"xiaohongshu", "zhihu", "weibo", "douyin", "zsxq"})
_VALID_ALL_PLATFORMS = _VALID_ECOMMERCE | _VALID_CONTENT
_VALID_SEARCH_CONTENT = _VALID_CONTENT - {"zsxq"}
_VALID_SEARCH_PLATFORMS = _VALID_ECOMMERCE | _VALID_SEARCH_CONTENT
_VALID_HARVEST_PLATFORMS = _VALID_ALL_PLATFORMS | {"all"}


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


def _validate_platform(platform: str) -> str:
    """Validate and clean a platform name for harvest_cookies/guided_login. Raises ValidationError."""
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


def _validate_platforms(
    platforms, valid_set: frozenset[str] | None = None, default: frozenset[str] | None = None
) -> list[str]:
    """Validate and filter a platform whitelist. Raises ValidationError on bad types.

    Returns deduplicated list of valid platform names.
    Falls back to *default* if platforms is None or empty after filtering.
    """
    if valid_set is None:
        valid_set = _VALID_ALL_PLATFORMS
    if default is None:
        default = valid_set

    if platforms is None:
        return sorted(default)

    if not isinstance(platforms, (list, tuple)):
        raise ValidationError(
            f"platforms must be a list or None, got {type(platforms).__name__}",
            hint="Pass a list of platform names (e.g. ['taobao', 'jd']) or omit for all.",
        )

    seen: set[str] = set()
    out: list[str] = []
    for p in platforms:
        if not isinstance(p, str):
            continue
        p_clean = p.strip().lower()
        if p_clean in valid_set and p_clean not in seen:
            seen.add(p_clean)
            out.append(p_clean)
    return out if out else []  # empty = no valid platforms requested


# ═══════════════════════════════════════════════════════════════
# Engine error dict interceptor — maps raw engine error dicts to
# unified {"ok": false, "error": {...}} responses.
# ═══════════════════════════════════════════════════════════════

# Data keys that indicate a genuine success result (not an error dict).
_SUCCESS_KEYS: frozenset[str] = frozenset({
    "items", "topics", "count", "total", "platforms", "sections",
    "id", "noteId", "goodsId", "goods_id", "name", "user", "group_id",
    "title", "desc", "likes", "price", "sales", "status", "note_id",
    "succeeded",
})


def _is_success_result(result: dict) -> bool:
    """Return True if *result* looks like a success payload rather than an engine error dict.

    Engine error dicts carry ``{"error": "..."}`` with no real data keys.
    This returns False for those so the caller can map them to unified errors.
    """
    if not isinstance(result, dict):
        return True
    if "error" not in result:
        return True
    # zsxq explicit failure marker
    if result.get("succeeded") is False:
        return False
    # Any real data key present → success (the "error" is incidental)
    return any(k in result for k in _SUCCESS_KEYS)


def _handle_engine_error(result: dict) -> dict:
    """Convert a raw engine error dict to the unified ``error_response()`` format.

    Inspects ``result["error"]`` and maps to the appropriate ScraperError subclass.
    """
    err_msg = str(result.get("error", ""))
    if "登录" in err_msg or "login" in err_msg.lower():
        return error_response(SessionExpiredError(message=err_msg[:200]))
    if "HTTP" in err_msg or "搜索" in err_msg or "search" in err_msg.lower():
        return error_response(PlatformError(message=err_msg[:200]))
    if "Chrome" in err_msg or "浏览器" in err_msg or "page target" in err_msg:
        return error_response(BrowserUnavailableError(message=err_msg[:200]))
    return error_response(PlatformError(message=err_msg[:200]))


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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import TaobaoAPIError, TaobaoAuthError, TaobaoEngine
        engine = TaobaoEngine()
        return engine.search(keyword, limit=limit)
    except ValidationError as e:
        return error_response(e)
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
            SessionExpiredError(
                message="淘宝登录已过期",
                hint="在浏览器中重新登录淘宝，导出的新的 cookie 文件替换旧文件，或使用 guided_login('taobao') 自动收割。",
            )
        )
    except TaobaoAPIError:
        record_error(TaobaoAPIError("淘宝 API 返回错误"))
        return error_response(
            PlatformError(
                message="淘宝 API 返回错误",
                hint="淘宝 MTOP API 返回了异常响应，可能接口已变更，请稍后重试。",
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import JDCaptchaError, JDEngine, JDLoginWallError
        result = JDEngine().search(keyword, limit=limit)

        # ── post-process engine error dicts ──────────────
        if isinstance(result, dict) and result.get("error"):
            error_msg = result.get("error", "")
            if "无法启动" in error_msg or "浏览器未启动" in error_msg:
                raise BrowserUnavailableError(
                    message="京东浏览器不可用",
                    hint=result.get("hint", "请确保 Chrome 已安装。"),
                )
            if "Connection refused" in error_msg or "CDP" in error_msg:
                raise CDPUnavailableError(
                    message="京东 CDP 连接失败",
                    hint="请检查 Chrome DevTools 端口。",
                )
            # Generic engine error → PlatformError
            raise PlatformError(
                message=error_msg[:200],
                hint=result.get("hint", "京东引擎返回异常。"),
            )

        return result
    except ValidationError as e:
        return error_response(e)
    except JDLoginWallError as e:
        record_error(e)
        return error_response(
            SessionExpiredError(
                message="京东登录墙 — 需要重新登录",
                hint="京东检测到未登录状态。请在弹窗 Chrome 中手动登录 jd.com 后重试。",
            )
        )
    except JDCaptchaError as e:
        record_error(e)
        return error_response(
            CaptchaRequiredError(
                message="京东验证码",
                hint="京东弹出验证码。请在弹窗 Chrome 中手动完成验证后重试。",
            )
        )
    except BrowserUnavailableError as e:
        record_error(e)
        return error_response(e)
    except CDPUnavailableError as e:
        record_error(e)
        return error_response(e)
    except FileNotFoundError as e:
        record_error(e)
        return error_response(
            BrowserUnavailableError(
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import PDDAuthError, PDDEngine, PDDRateLimitError
        engine = PDDEngine()
        result = engine.search(keyword, limit=limit)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
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
            SessionExpiredError(
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
            BrowserUnavailableError(
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=5)

        platforms = _validate_platforms(
            platforms, frozenset({"taobao", "jd", "pdd"}), default=frozenset({"taobao", "jd"})
        )

        from cn_scraper_mcp.compare import compare_prices as _compare
        return _compare(keyword, platforms=platforms, limit=limit)
    except ValidationError as e:
        return error_response(e)
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
    try:
        if not isinstance(url_or_id, str) or not url_or_id.strip():
            raise ValidationError(
                "url_or_id must be a non-empty string",
                hint="Provide a PDD goods_id (e.g. '123456789') or goods2.html URL.",
            )

        from cn_scraper_mcp.engines import PDDAuthError, PDDEngine, PDDSoldOutError
        engine = PDDEngine()
        result = engine.product_detail(url_or_id.strip())
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
    except PDDAuthError as e:
        record_error(e)
        return error_response(
            SessionExpiredError(
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
            BrowserUnavailableError(
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        result = engine.search(keyword, limit=limit)

        # ── post-process engine state dicts ──────────────
        if isinstance(result, dict) and "state" in result:
            state = result.get("state", "")
            if state == "login_expired":
                raise SessionExpiredError(
                    message="小红书登录已过期",
                    hint="在浏览器中重新登录小红书，或使用 guided_login('xiaohongshu') 自动收割。",
                )
            if state == "ip_risk":
                raise RiskControlledError(
                    message="小红书 IP 被风控",
                    hint="当前 IP 被小红书标记为风险。请更换网络后重试。",
                )
            if state == "captcha":
                raise CaptchaRequiredError(
                    message="小红书弹出验证码",
                    hint="请在浏览器中手动完成小红书验证码后重试。",
                )
            if state == "error":
                error_code = result.get("error_code", "")
                if "BROWSER" in error_code:
                    raise BrowserUnavailableError(
                        message="小红书浏览器不可用",
                        hint=result.get("hint", "请确保 Chrome 已安装。"),
                    )
                raise PlatformError(
                    message=result.get("error_code", "小红书搜索异常"),
                    hint=result.get("hint", "小红书引擎返回异常。"),
                )

        return result
    except ValidationError as e:
        return error_response(e)
    except SessionExpiredError as e:
        record_error(e)
        return error_response(e)
    except RiskControlledError as e:
        record_error(e)
        return error_response(e)
    except CaptchaRequiredError as e:
        record_error(e)
        return error_response(e)
    except BrowserUnavailableError as e:
        record_error(e)
        return error_response(e)
    except PlatformError as e:
        record_error(e)
        return error_response(e)
    except FileNotFoundError as e:
        record_error(e)
        return error_response(
            BrowserUnavailableError(
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
    try:
        note_id = _validate_note_id(note_id)

        from cn_scraper_mcp.engines import XiaohongshuEngine
        engine = XiaohongshuEngine()
        result = engine.get_note(note_id)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import ZhihuEngine
        result = ZhihuEngine().search(keyword, limit=limit)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
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
        result = ZhihuEngine().hot_list()
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import WeiboEngine
        result = WeiboEngine().search(keyword, limit=limit)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
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
        result = WeiboEngine().hot_list()
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def weibo_user_timeline(uid: str, limit: int = 10) -> dict:
    """获取微博用户时间线（最近发言）。

    需要登录 cookie（SUB token）。
    可通过 weibo_search 找到目标用户获取其 UID。

    Args:
        uid: 用户 ID（数字，如 "2803301701" = 人民日报）
        limit: 返回帖子数 (默认 10)

    Returns:
        {uid, user, count, items: [{id, text, user, attitudes, comments, reposts, created_at, url}]}
    """
    try:
        uid = str(uid).strip()
        if not uid or not uid.isdigit():
            raise ValidationError(
                "uid 必须是数字",
                hint="Provide a numeric user ID (e.g. '2803301701').",
            )

        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines import WeiboEngine
        result = WeiboEngine().user_timeline(uid, limit=limit)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def douyin_search(keyword: str, limit: int = 10) -> dict:
    """搜索抖音视频 — CDP 浏览器自动轮询，验证码自动等待。

    需要 Chrome 已登录抖音（用 guided_login 先登录）。
    弹出验证码时持续等待你手动过，通过后自动抓取结果。

    Args:
        keyword: 搜索关键词
        limit: 返回条数 (默认 10)
    """
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)
        from cn_scraper_mcp.engines import DouyinEngine
        result = DouyinEngine().search(keyword, limit=limit)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def douyin_hot_list() -> dict:
    """抖音实时热搜榜。需要登录 cookie（首次用 guided_login 收割）。

    Returns:
        {count, items: [{word, hot_value, position, label}]}
    """
    try:
        from cn_scraper_mcp.engines import DouyinEngine
        result = DouyinEngine().hot_list()
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
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
    try:
        group_id = _validate_group_id(group_id)
        count = _validate_count(count, default=5)

        from cn_scraper_mcp.engines import ZsxqEngine
        result = ZsxqEngine().get_topics(group_id, count=count, owner_only=owner_only)
        if isinstance(result, dict) and "error" in result and not _is_success_result(result):
            return _handle_engine_error(result)
        return result
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Cookie harvest — CDP-based auto-extraction from user's browser
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def harvest_cookies(platform: str, port: int | None = None) -> dict:
    """从用户自己的浏览器会话中自动提取 cookie（包括 HttpOnly cookie）。

    通过 Chrome DevTools Protocol (CDP) 的 Network.getAllCookies 提取
    浏览器 cookie jar 中的所有 cookie（含 HttpOnly，这是 JS 无法获取的）。
    仅提取用户**自己**的浏览器会话——浏览器须已在指定端口运行且已登录。

    提取的 cookie 保存到 ~/.cn-scraper-cookies/<platform>.json。

    Args:
        platform: 平台名 — 'taobao', 'xiaohongshu', 'zhihu', 'zsxq', 'jd', 'pdd', 'weibo', 'douyin'
        port:     CDP 调试端口 (可选, 各平台有默认值)

    Returns:
        {platform, count, saved_to, status}
    """
    try:
        platform = _validate_platform(platform)
        if port is not None and (not isinstance(port, int) or port < 1024 or port > 65535):
            raise ValidationError(
                f"port must be between 1024 and 65535, got {port}",
                hint="Provide a valid CDP debug port number.",
            )

        from cn_scraper_mcp.cookie_harvest import CookieHarvester, CookieHarvestError
        harvester = CookieHarvester()
        return harvester.harvest(platform, port=port)
    except ValidationError as e:
        return error_response(e)
    except CookieHarvestError as e:
        record_error(e)
        return error_response(
            CDPUnavailableError(
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


@mcp.tool()
def guided_login(platform: str, port: int | None = None) -> dict:
    """打开浏览器让你登录平台，自动检测登录态并收割 Cookie。

    自动打开 Chrome → 导航到平台登录页 → 你扫码/输入密码 →
    检测到你登录成功后自动收割 Cookie 并保存。

    无需手动操作 CDP 端口——全程自动化。

    Args:
        platform: 平台名 — 'taobao', 'xiaohongshu', 'zhihu', 'zsxq', 'jd', 'weibo', 'pdd', 'douyin'
        port:     CDP 端口 (可选, 默认 9222)

    Returns:
        {platform, count, saved_to, status, method: 'guided_login'}
    """
    try:
        platform = _validate_platform(platform)
        if port is not None and (not isinstance(port, int) or port < 1024 or port > 65535):
            raise ValidationError(
                f"port must be between 1024 and 65535, got {port}",
                hint="Provide a valid CDP debug port number.",
            )

        from cn_scraper_mcp.cookie_harvest import guided_login as _guided_login
        return _guided_login(platform, port=port)
    except ValidationError as e:
        return error_response(e)
    except ValueError as e:
        record_error(e)
        return error_response(ValidationError(message=str(e)))
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
    try:
        from cn_scraper_mcp.auth import check_all_cookies
        return check_all_cookies()
    except Exception as e:
        record_error(e)
        return error_response(e)


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
    try:
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
    except Exception as e:
        record_error(e)
        return error_response(e)


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
# Aggregate cross-platform tools (ROADMAP §6)
# ═══════════════════════════════════════════════════════════════

@mcp.tool()
def search_all(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
    timeout: float = 30.0,
) -> dict:
    """跨平台全量搜索 — 在所有（或指定）平台并发搜索同一关键词。

    覆盖 7 个支持关键词搜索的平台：taobao, jd, pdd, xiaohongshu, zhihu, weibo, douyin。
    知识星球使用独立的 `zsxq_topics(group_id)` 工具，不接受关键词搜索。
    单平台失败不影响其他平台结果。结果按类型标准化（ProductItem / ContentItem）。

    Args:
        keyword:   搜索关键词
        platforms: 平台白名单 (默认全部 7 个关键词搜索平台)
        limit:     每平台最多返回条数 (默认 5)
        timeout:   全局超时秒数 (默认 30)

    Returns:
        {keyword, platforms: {taobao: {status, items}, jd: {...}, ...}}
    """
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=5)
        if timeout <= 0:
            timeout = 30.0
        platforms = _validate_platforms(
            platforms, _VALID_SEARCH_PLATFORMS, _VALID_SEARCH_PLATFORMS
        )
        if platforms is not None and len(platforms) == 0:
            return error_response(
                ValidationError(
                    "没有有效的平台",
                    hint=f"支持的平台: {', '.join(sorted(_VALID_SEARCH_PLATFORMS))}",
                )
            )

        from cn_scraper_mcp.aggregate import search_all as _search_all
        return _search_all(keyword, platforms=platforms, limit=limit, timeout=timeout)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def search_products(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """跨平台电商搜索 — 淘宝/京东/拼多多并发搜索，含价格对比。

    返回统一 ProductItem 格式，附带跨平台价格区间和中位数。

    Args:
        keyword:   搜索关键词
        platforms: 电商平台白名单 (默认全部: taobao, jd, pdd)
        limit:     每平台最多返回条数 (默认 5)

    Returns:
        {keyword, platforms: {taobao: {status, items}, ...}, price_comparison: {price_range, median}}
    """
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=5)
        platforms = _validate_platforms(platforms, _VALID_ECOMMERCE, _VALID_ECOMMERCE)
        if platforms is not None and len(platforms) == 0:
            return error_response(
                ValidationError(
                    "没有有效的电商平台",
                    hint="支持的电商平台: taobao, jd, pdd",
                )
            )

        from cn_scraper_mcp.aggregate import search_products as _search_products
        return _search_products(keyword, platforms=platforms, limit=limit)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def search_content(
    keyword: str,
    platforms: list[str] | None = None,
    limit: int = 5,
) -> dict:
    """跨平台内容搜索 — 小红书/知乎/微博/抖音并发搜索。

    返回统一 ContentItem 格式。

    Args:
        keyword:   搜索关键词
        platforms: 内容平台白名单 (默认全部: xiaohongshu, zhihu, weibo, douyin)
        limit:     每平台最多返回条数 (默认 5)

    Returns:
        {keyword, platforms: {xiaohongshu: {status, items}, ...}}
    """
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=5)
        platforms = _validate_platforms(
            platforms, _VALID_SEARCH_CONTENT, _VALID_SEARCH_CONTENT
        )
        if platforms is not None and len(platforms) == 0:
            return error_response(
                ValidationError(
                    "没有有效的内容平台",
                    hint=f"支持的内容平台: {', '.join(sorted(_VALID_SEARCH_CONTENT))}",
                )
            )

        from cn_scraper_mcp.aggregate import search_content as _search_content
        return _search_content(keyword, platforms=platforms, limit=limit)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def get_trending(
    platforms: list[str] | None = None,
) -> dict:
    """跨平台热搜聚合 — 微博热搜/知乎热榜/抖音热搜并发获取。

    返回统一 TrendItem 格式。

    Args:
        platforms: 热搜平台白名单 (默认全部: weibo, zhihu, douyin)

    Returns:
        {platforms: {weibo: {status, items: [{rank, word, hot_metric, url, label}]}, ...}}
    """
    try:
        from cn_scraper_mcp.aggregate import HOTLIST_PLATFORMS
        from cn_scraper_mcp.aggregate import get_trending as _get_trending
        hotlist_set = frozenset(HOTLIST_PLATFORMS)
        platforms = _validate_platforms(platforms, hotlist_set, hotlist_set)
        if platforms is not None and len(platforms) == 0:
            return error_response(
                ValidationError(
                    "没有有效的热搜平台",
                    hint=f"支持的热搜平台: {', '.join(sorted(hotlist_set))}",
                )
            )
        return _get_trending(platforms=platforms)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    """Entry point for `cn-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
