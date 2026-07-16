"""Weibo (微博) search engine.

Weibo has moderate anti-bot protection:

    - Hot list:  ✅ guest-accessible via weibo.com/ajax/side/hotSearch (no login)
    - Search:    🔒 requires login cookies (SUB cookie)
                 Both mobile API (m.weibo.cn) and desktop API (weibo.com/ajax/search)
                 return ok:-100 without valid cookies.

Requirements (for search):
    - Cookie file: $WEIBO_COOKIES_FILE or ~/.cn-scraper-cookies/weibo.json
    - Key cookie: SUB (Weibo login session token)

DISCLAIMER: Weibo's guest API endpoints may change or be restricted at any time.
The hot list endpoint currently works without auth (verified 2026-07).
Search requires cookies and may break without warning.
"""

import re
import urllib.parse

from cn_scraper_mcp.auth import CookieFileManager
from cn_scraper_mcp.errors import (
    AuthRequiredError,
    CookieExpiredError,
    PlatformError,
    technical_error_from_http,
)
from cn_scraper_mcp.http import HttpClient

# ── HTML cleaning ────────────────────────────────────────────────────

_HTML_RE = re.compile(r"<[^>]+>")


def _clean_html(text: str) -> str:
    """Strip HTML tags and trim whitespace."""
    if not text:
        return ""
    return _HTML_RE.sub("", text).strip()


class WeiboEngine:
    """Search and hot-list Weibo (微博).

    Two modes:
    - Hot list: guest-accessible via weibo.com/ajax/side/hotSearch
    - Search: requires login cookies (SUB token) via weibo.com/ajax/statuses/search

    Usage:
        engine = WeiboEngine(cookies_path="~/.cn-scraper-cookies/weibo.json")
        hot = engine.hot_list()
        results = engine.search("华为", limit=10)  # requires cookies
    """

    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
    )

    SEARCH_URL = "https://weibo.com/ajax/statuses/search"  # desktop API (weibo.com cookies work)
    HOT_LIST_URL = "https://weibo.com/ajax/side/hotSearch"

    def __init__(self, cookies_path: str | None = None):
        mgr = CookieFileManager("weibo", cookies_path=cookies_path)
        self.cookies = mgr.load()

        self.cookies_path = mgr.resolve_path()

        # Shared HTTP client with retry/backoff/rate-limit
        self.http = HttpClient(
            timeout=15,
            max_retries=3,
            backoff_base=1.0,
            rate_limit_interval=0.5,
            default_headers={
                "User-Agent": self.UA,
                "Accept": "application/json",
            },
        )

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # ── search ─────────────────────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Weibo posts via mobile API. **Requires login cookies (SUB).**

        Without cookies, Weibo returns ok:-100 (login redirect).
        With valid cookies, the mobile API returns card-based JSON with mblog objects.

        Args:
            keyword: Search query
            limit: Max posts to return (default 10)

        Returns:
            {
                "keyword": str,
                "items": [
                    {
                        "id": str,           # mid (微博ID)
                        "text": str,          # Cleaned text (HTML stripped)
                        "user": str,          # screen_name
                        "attitudes": int,     # 赞
                        "comments": int,      # 评论
                        "reposts": int,       # 转发
                        "created_at": str,    # 发布时间
                        "url": str,           # https://m.weibo.cn/detail/{id}
                    }
                ]
            }
        """
        if not self.cookies:
            return {
                "keyword": keyword,
                "error": "微博搜索需要登录",
                "hint": (
                    "微博搜索需要登录 cookies（SUB token）。\n"
                    "请从已登录的 weibo.com 导出 cookies 到 "
                    "~/.cn-scraper-cookies/weibo.json\n"
                    "或用 harvest_cookies 工具自动收割。"
                ),
            }

        headers = {
            "User-Agent": self.UA,
            "Referer": "https://weibo.com/",
            "Cookie": self._cookie_str(),
        }

        status, data = self.http.get_json(
            self.SEARCH_URL,
            params={"q": keyword, "page": 1},
            headers=headers,
        )

        if status == 0:
            return {"keyword": keyword, "error": data.get("error", "搜索请求失败")}

        if status >= 400:
            return {"keyword": keyword, "error": f"HTTP {status}"}

        # Desktop API returns {ok: 1, data: {statuses: [...]}}
        if data.get("ok") != 1:
            return {
                "keyword": keyword,
                "error": "微博搜索需要登录" if data.get("ok") == -100 else f"API ok={data.get('ok')}",
                "hint": "请用 harvest_cookies 收割 weibo.com 的登录 cookie。" if data.get("ok") == -100 else "",
            }

        statuses = data.get("data", {}).get("statuses", []) or data.get("statuses", [])
        items = []
        for s in statuses:
            mid = str(s.get("mid", "") or s.get("id", ""))
            raw_text = s.get("text_raw", "") or s.get("text", "")
            clean_text = _clean_html(raw_text)
            user = s.get("user", {})

            items.append({
                "id": mid,
                "text": clean_text,
                "user": user.get("screen_name", ""),
                "user_id": str(user.get("id", "")),
                "attitudes": s.get("attitudes_count", 0),
                "comments": s.get("comments_count", 0),
                "reposts": s.get("reposts_count", 0),
                "created_at": s.get("created_at", ""),
                "url": f"https://weibo.com/{user.get('id', '')}/{mid}" if mid else "",
            })

        items = items[:limit]
        return {"keyword": keyword, "count": len(items), "items": items}

    # ── hot list ────────────────────────────────────────────────────

    def hot_list(self) -> dict:
        """Get current Weibo trending topics (hot search list).

        **Guest-accessible** — no login required.
        Uses weibo.com/ajax/side/hotSearch endpoint.

        Returns:
            {
                "items": [
                    {
                        "rank": int,       # 排名 (1-based)
                        "word": str,        # 话题词
                        "num": int,         # 热度数
                        "url": str,         # 搜索链接
                        "note": str,        # 备注说明
                        "label": str,       # 标签 (e.g. "热", "爆", "新")
                    }
                ],
                "hotgov": dict | None,     # Pinned government topic
            }
        """
        headers = {
            "Referer": "https://weibo.com/",
            "X-Requested-With": "XMLHttpRequest",
        }

        status, data = self.http.get_json(self.HOT_LIST_URL, headers=headers)

        if status == 0:
            return {
                "error": data.get("error", "获取热搜失败"),
            }

        if status >= 400:
            return {
                "error": f"HTTP {status}: {data.get('error', 'Unknown error')}",
            }

        data_section = data.get("data", {})

        # Parse realtime hot list
        realtime = data_section.get("realtime", [])
        items = []
        for item in realtime:
            word = item.get("word", "")
            items.append({
                "rank": item.get("realpos", 0),
                "word": word,
                "num": item.get("num", 0),
                "url": f"https://s.weibo.com/weibo?q={urllib.parse.quote(word)}",
                "note": item.get("note", ""),
                "label": item.get("label_name", ""),
            })

        # Parse hotgov (pinned government topic)
        hotgov = data_section.get("hotgov")
        hotgov_parsed = None
        if hotgov:
            hotgov_parsed = {
                "name": hotgov.get("name", ""),
                "word": hotgov.get("word", ""),
                "url": hotgov.get("url", ""),
                "note": hotgov.get("note", ""),
            }

        return {
            "count": len(items),
            "items": items,
            "hotgov": hotgov_parsed,
        }

    # ── user timeline ────────────────────────────────────────────────

    def user_timeline(self, uid: str, limit: int = 10) -> dict:
        """Get a user's recent posts (timeline).

        Args:
            uid: User ID (numeric, from search results or profile URL)
            limit: Max posts to return (default 10)

        Returns:
            {"uid": str, "user": str, "count": int, "items": [{id, text, ...}]}
        """
        if not self.cookies:
            return {
                "error": "用户时间线需要登录",
                "hint": "请提供 weibo.com 的登录 cookie（SUB token）。",
            }

        headers = {
            "User-Agent": self.UA,
            "Referer": f"https://weibo.com/u/{uid}",
            "Cookie": self._cookie_str(),
        }

        status, data = self.http.get_json(
            "https://weibo.com/ajax/statuses/mymblog",
            params={"uid": uid, "page": 1, "feature": 0},
            headers=headers,
        )

        if status == 0:
            return {"error": data.get("error", "请求失败"), "uid": uid}

        if status >= 400:
            return {"error": f"HTTP {status}", "uid": uid}

        if data.get("ok") != 1:
            return {"error": f"API ok={data.get('ok')}", "uid": uid}

        posts = data.get("data", {}).get("list", [])
        items = []
        for p in posts:
            mid = str(p.get("mid", "") or p.get("id", ""))
            raw_text = p.get("text_raw", "") or p.get("text", "")
            clean_text = _clean_html(raw_text)
            user = p.get("user", {})

            items.append({
                "id": mid,
                "text": clean_text,
                "user": user.get("screen_name", ""),
                "attitudes": p.get("attitudes_count", 0),
                "comments": p.get("comments_count", 0),
                "reposts": p.get("reposts_count", 0),
                "created_at": p.get("created_at", ""),
                "url": f"https://weibo.com/{user.get('id', uid)}/{mid}" if mid else "",
            })

        user_name = items[0]["user"] if items else ""

        return {
            "uid": uid,
            "user": user_name,
            "count": len(items[:limit]),
            "items": items[:limit],
        }

    # ── comments ────────────────────────────────────────────────────

    def get_comments(self, mid: int | str, limit: int = 20) -> dict:
        """Get first-page comments for a Weibo post.

        Args:
            mid: Post ID (from search results or user_timeline items).
            limit: Max comments to return (default 20).

        Returns:
            {mid, count, comments: [{id, content, user, likes, time}]}
        """
        if not self.cookies:
            raise AuthRequiredError(
                "微博评论需要登录",
                hint="请提供 weibo.com 的登录 cookie（SUB token）",
            )

        headers = {
            "User-Agent": self.UA,
            "Referer": "https://weibo.com/",
            "Cookie": self._cookie_str(),
        }

        status, data = self.http.get_json(
            "https://weibo.com/ajax/statuses/buildComments",
            params={
                "id": str(mid),
                "is_reload": "1",
                "is_show_bulletin": "2",
                "is_mix": "0",
                "count": str(limit),
                "fetch_level": "0",
            },
            headers=headers,
        )

        if status == 0 or status >= 400:
            raise technical_error_from_http("weibo", status)

        if data.get("ok") != 1:
            if data.get("ok") == -100:
                raise CookieExpiredError(
                    "微博拒绝了缓存登录态",
                    hint="请用 harvest_cookies 收割 weibo.com 的登录 Cookie。",
                )
            raise PlatformError(f"微博评论 API 返回 ok={data.get('ok')}")

        comments = []
        for c in (data.get("data", []) or [])[:limit]:
            user = c.get("user", {}) or {}
            comments.append({
                "id": str(c.get("idstr", "") or c.get("id", "")),
                "content": _clean_html(c.get("text_raw", "") or c.get("text", "")),
                "user": user.get("screen_name", ""),
                "user_id": str(user.get("id", "")),
                "likes": c.get("like_counts", 0) or c.get("like_count", 0),
                "time": c.get("created_at", ""),
            })

        return {
            "mid": str(mid),
            "count": len(comments),
            "comments": comments,
        }
