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

import json
import os
import re
import urllib.parse
from pathlib import Path

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
    - Search: requires login cookies (SUB token) via m.weibo.cn API

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

    SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"
    HOT_LIST_URL = "https://weibo.com/ajax/side/hotSearch"

    def __init__(self, cookies_path: str | None = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "WEIBO_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "weibo.json")
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            try:
                self.cookies = json.load(open(cookies_path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.cookies = {}

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
                    "微博搜索 API 需要登录 cookies（SUB token）。\n"
                    "请从已登录的浏览器导出 cookies 到 "
                    "~/.cn-scraper-cookies/weibo.json\n"
                    "导出方法: 浏览器 DevTools → Application → Cookies → weibo.com → "
                    "复制 SUB 字段的值。"
                ),
            }

        enc = urllib.parse.quote(keyword)
        # containerid = "100103type=1&q=<keyword>" — the = and & must be URL-encoded
        container_id = f"100103type%3D1%26q%3D{enc}"

        headers = {
            "User-Agent": self.MOBILE_UA,
            "Referer": "https://m.weibo.cn/",
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": self._cookie_str(),
        }

        status, data = self.http.get_json(
            self.SEARCH_URL,
            params={"containerid": container_id},
            headers=headers,
        )

        if status == 0:
            return {
                "keyword": keyword,
                "error": data.get("error", "搜索请求失败"),
            }

        if status >= 400:
            return {
                "keyword": keyword,
                "error": f"HTTP {status}: {data.get('error', 'Unknown error')}",
            }

        # Check ok field
        if data.get("ok") == -100:
            return {
                "keyword": keyword,
                "error": "微博搜索需要登录",
                "hint": (
                    "微博 API 返回 ok:-100（未登录）。\n"
                    "请提供有效的 cookies（SUB token）到 "
                    "~/.cn-scraper-cookies/weibo.json"
                ),
            }

        if data.get("ok") != 1:
            return {
                "keyword": keyword,
                "error": f"API 返回 ok={data.get('ok')}",
                "msg": data.get("msg", ""),
            }

        # Parse cards
        cards = data.get("data", {}).get("cards", [])
        items = []
        for card in cards:
            if card.get("card_type") != 9:
                continue  # card_type 9 = mblog (微博帖子)

            mblog = card.get("mblog", {})
            if not mblog:
                continue

            mid = mblog.get("mid", "") or mblog.get("id", "")

            # Clean HTML from text
            raw_text = mblog.get("text", "")
            clean_text = _clean_html(raw_text)

            user_info = mblog.get("user", {})

            items.append({
                "id": str(mid),
                "text": clean_text,
                "user": user_info.get("screen_name", ""),
                "attitudes": mblog.get("attitudes_count", 0),
                "comments": mblog.get("comments_count", 0),
                "reposts": mblog.get("reposts_count", 0),
                "created_at": mblog.get("created_at", ""),
                "url": f"https://m.weibo.cn/detail/{mid}" if mid else "",
            })

        # Apply limit
        items = items[:limit]

        return {
            "keyword": keyword,
            "count": len(items),
            "items": items,
        }

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
