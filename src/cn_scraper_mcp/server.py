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
    zhihu_comments    — 知乎回答评论
    weibo_search      — 微博搜索 (REST API, 需登录 cookie)
    weibo_hot_list    — 微博热搜榜 (无需登录!)
    weibo_comments    — 微博帖子评论
    douyin_search     — 抖音搜索 (⚠️ 实验性, 当前不可用)
    zsxq_topics       — 知识星球帖子 (REST API)
    check_cookies     — 检查所有平台 cookie 状态
    verify_login      — 远端验证缓存登录态
    diagnose          — 环境诊断
    harvest_cookies   — 从用户浏览器通过 CDP 自动提取 cookie (含 HttpOnly)

Start:
    cn-scraper-mcp
    python -m cn_scraper_mcp.server
"""

from fastmcp import FastMCP

from cn_scraper_mcp import __version__
from cn_scraper_mcp.errors import (  # noqa: E402
    BrowserError,
    CookieExpiredError,
    CookieMissingError,
    PlatformError,
    RateLimitError,
    ValidationError,
    error_response,
)
from cn_scraper_mcp.logging import get_logger, record_error
from cn_scraper_mcp.validation import (
    VALID_AUTH_PLATFORMS,
    validate_port,
)
from cn_scraper_mcp.validation import (
    validate_answer_id as _validate_answer_id,
)
from cn_scraper_mcp.validation import (
    validate_count as _validate_count,
)
from cn_scraper_mcp.validation import (
    validate_group_id as _validate_group_id,
)
from cn_scraper_mcp.validation import (
    validate_keyword as _validate_keyword,
)
from cn_scraper_mcp.validation import (
    validate_limit as _validate_limit,
)
from cn_scraper_mcp.validation import (
    validate_mid as _validate_mid,
)
from cn_scraper_mcp.validation import (
    validate_note_id as _validate_note_id,
)
from cn_scraper_mcp.validation import (
    validate_platform as _validate_platform,
)
from cn_scraper_mcp.validation import (
    validate_xsec_token as _validate_xsec_token,
)

logger = get_logger("cn_scraper_mcp.server")

mcp = FastMCP(
    name="cn-scraper",
    version=__version__,
    instructions="""中文互联网爬虫工具 — 电商 + 内容平台全覆盖。

电商：taobao_search (纯脚本最快), jd_search (需要 Chrome), pdd_search (Chrome + ⚠️单次搜索限制)
社区：xiaohongshu_search/note/comments (需要本地 Chrome), zhihu_search/comments (REST API, 需登录), weibo_search/comments (REST API, 需登录)
热搜：weibo_hot_list (无需登录!), zhihu_hot_list (需登录)
付费社群：zsxq_topics (知识星球 API)
认证：check_cookies 检查本地缓存, verify_login 远端验证知乎/微博登录态
诊断：diagnose 查看环境诊断""",
)


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

        # ── execution ───────────────────────────────────
        from cn_scraper_mcp.engines.taobao import TaobaoAPIError, TaobaoAuthError, TaobaoEngine

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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines.jd import JDEngine

        return JDEngine().search(keyword, limit=limit)
    except ValidationError as e:
        return error_response(e)
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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines.pdd import PDDAuthError, PDDEngine, PDDRateLimitError

        engine = PDDEngine()
        return engine.search(keyword, limit=limit)
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
        from cn_scraper_mcp.engines.pdd import PDDAuthError, PDDEngine, PDDSoldOutError

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
    try:
        keyword = _validate_keyword(keyword)
        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

        engine = XiaohongshuEngine()
        return engine.search(keyword, limit=limit)
    except ValidationError as e:
        return error_response(e)
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
def xiaohongshu_note(note_id: str, xsec_token: str) -> dict:
    """获取小红书笔记详情（标题、正文、点赞、标签、评论）。

    note_id 和 xsec_token 都来自同一条 xiaohongshu_search 结果。

    Args:
        note_id: 笔记 ID（从 xiaohongshu_search 结果中的 noteId 字段）
        xsec_token: 反爬 token（从同一条 xiaohongshu_search 结果中的 xsec_token 字段）

    Returns:
        {id, title, desc, likes, collects, comments, tags, user, time}
    """
    try:
        note_id = _validate_note_id(note_id)

        from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

        engine = XiaohongshuEngine()
        return engine.get_note(note_id, xsec_token=xsec_token)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def xiaohongshu_comments(note_id: str, xsec_token: str) -> dict:
    """获取小红书笔记的评论（首屏，约 10-20 条）。

    需要本地 Chrome + XHS 登录 cookie。
    note_id 和 xsec_token 都来自同一条 xiaohongshu_search 结果。

    Args:
        note_id: 笔记 ID（16 位十六进制字符串）
        xsec_token: 同一搜索结果中的访问令牌（必填）

    Returns:
        {noteId, comments: [{content, userName, likes, time}]}
    """
    try:
        note_id = _validate_note_id(note_id)
        xsec_token = _validate_xsec_token(xsec_token)

        from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

        engine = XiaohongshuEngine()
        return engine.get_comments(note_id, xsec_token=xsec_token)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def zhihu_search(keyword: str, limit: int = 10) -> dict:
    """搜索知乎内容（问题/文章）。知乎已关闭游客搜索，需要有效登录 Cookie。

    无需浏览器——直接调知乎 v4 search API。
    需要 cookie: ~/.cn-scraper-cookies/zhihu.json（z_c0 + d_c0）

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

        from cn_scraper_mcp.engines.zhihu import ZhihuEngine

        return ZhihuEngine().search(keyword, limit=limit)
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
        from cn_scraper_mcp.engines.zhihu import ZhihuEngine

        return ZhihuEngine().hot_list()
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def zhihu_comments(answer_id: str, limit: int = 20) -> dict:
    """获取知乎回答的首屏评论。需要登录 cookie。

    answer_id 来自 zhihu_search 结果中 type 为 "answer" 的 item 的 id 字段。

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

    Args:
        answer_id: 回答 ID（从 zhihu_search 结果中的 id 字段）
        limit: 返回条数 (默认 20)

    Returns:
        {answer_id, count, comments: [{id, content, author, likes, time}]}
    """
    try:
        answer_id = _validate_answer_id(answer_id)
        limit = _validate_limit(limit, default=20)

        from cn_scraper_mcp.engines.zhihu import ZhihuEngine

        engine = ZhihuEngine()
        return engine.get_comments(answer_id, limit=limit)
    except ValidationError as e:
        return error_response(e)
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

        from cn_scraper_mcp.engines.weibo import WeiboEngine

        return WeiboEngine().search(keyword, limit=limit)
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
        from cn_scraper_mcp.engines.weibo import WeiboEngine

        return WeiboEngine().hot_list()
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
    uid = str(uid).strip()
    try:
        if not uid or not uid.isdigit():
            return {"error": "uid 必须是数字"}

        limit = _validate_limit(limit, default=10)

        from cn_scraper_mcp.engines.weibo import WeiboEngine

        return WeiboEngine().user_timeline(uid, limit=limit)
    except ValidationError as e:
        return error_response(e)
    except Exception as e:
        record_error(e)
        return error_response(e)


@mcp.tool()
def weibo_comments(mid: str, limit: int = 20) -> dict:
    """获取微博帖子的首屏评论。需要登录 cookie（SUB token）。

    mid 来自 weibo_search 或 weibo_user_timeline 结果中 item 的 id 字段。

    并发安全: ✅ 纯 HTTP/REST API，无共享状态，任意并发调用安全。

    Args:
        mid: 微博帖子 ID（从 weibo_search/user_timeline 结果中的 id 字段）
        limit: 返回条数 (默认 20)

    Returns:
        {mid, count, comments: [{id, content, user, user_id, likes, time}]}
    """
    try:
        mid = _validate_mid(mid)
        limit = _validate_limit(limit, default=20)

        from cn_scraper_mcp.engines.weibo import WeiboEngine

        engine = WeiboEngine()
        return engine.get_comments(mid, limit=limit)
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
        from cn_scraper_mcp.engines.douyin import DouyinEngine

        return DouyinEngine().search(keyword, limit=limit)
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
        from cn_scraper_mcp.engines.douyin import DouyinEngine

        return DouyinEngine().hot_list()
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

        from cn_scraper_mcp.engines.zsxq import ZsxqEngine

        return ZsxqEngine().get_topics(group_id, count=count, owner_only=owner_only)
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

    非 profile 平台的 Cookie 保存到认证注册表指定的 JSON 文件。
    京东使用持久化 Chrome profile，请改用 guided_login。

    Args:
        platform: 认证注册表中的平台名。京东会返回 profile_required。
        port:     CDP 调试端口 (可选, 各平台有默认值)

    Returns:
        {platform, count, saved_to, status}
    """
    try:
        platform = _validate_platform(platform)
        port = validate_port(port)

        from cn_scraper_mcp.cookie_harvest import CookieHarvester, CookieHarvestError

        harvester = CookieHarvester()
        return harvester.harvest(platform, port=port)
    except ValidationError as e:
        return error_response(e)
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
                hint=f"Supported platforms: {', '.join(sorted(VALID_AUTH_PLATFORMS))}",
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
        platform: 认证注册表中的平台名，包括京东、微博和抖音。
        port:     CDP 端口 (可选, 默认 9222)

    Returns:
        {platform, count, saved_to, status, method: 'guided_login'}
    """
    try:
        platform = _validate_platform(platform)
        port = validate_port(port)

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
    from cn_scraper_mcp.auth import check_all_cookies

    return check_all_cookies()


@mcp.tool()
def verify_login(platform: str) -> dict:
    """远端验证缓存登录态是否仍被平台接受。

    这与 check_cookies 的本地文件检查不同：本工具会发起只读在线请求。
    当前可真实远端验证知乎和微博；其他平台明确返回 unsupported，不会把
    “Cookie 文件存在”误报为“登录有效”。

    Args:
        platform: 认证注册表中的平台名。

    Returns:
        {platform, cache_state, verified, remote_state}
    """
    try:
        platform = _validate_platform(platform)
        from cn_scraper_mcp.auth_verify import verify_login as _verify_login

        return _verify_login(platform)
    except ValidationError as exc:
        return error_response(exc)
    except Exception as exc:
        record_error(exc)
        return error_response(exc)


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
    from cn_scraper_mcp.diagnostics import diagnose_environment

    return diagnose_environment()


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


def main():
    """Entry point for `cn-scraper-mcp` CLI command."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
