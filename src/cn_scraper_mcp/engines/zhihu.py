"""Zhihu (知乎) search engine.

知乎 has moderate anti-bot: guest search works with a mobile UA on curl,
but logged-in content and full articles require cookies.

Requirements (for full access):
    - Cookie file: $ZHIHU_COOKIES_FILE or ~/.cn-scraper-cookies/zhihu.json
    - Key cookies: z_c0, d_c0 (auth)
"""

import re
import urllib.parse

from cn_scraper_mcp.auth import CookieFileManager
from cn_scraper_mcp.http import HttpClient


class ZhihuEngine:
    """Search Zhihu (知乎) for content.

    Two modes:
    - Guest: curl with mobile UA (limited results, no logged-in content)
    - Logged-in: cookies from a browser session (full access)

    Usage:
        engine = ZhihuEngine(cookies_path="~/.cn-scraper-cookies/zhihu.json")
        results = engine.search("半导体 投资", limit=10)
    """

    UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1")

    def __init__(self, cookies_path: str | None = None):
        mgr = CookieFileManager("zhihu", cookies_path=cookies_path)
        self.cookies = mgr.load()

        self.cookies_path = mgr.resolve_path()

        # Shared HTTP client with retry/backoff/rate-limit
        self.http = HttpClient(
            timeout=15,
            max_retries=3,
            backoff_base=1.0,
            rate_limit_interval=0.5,
            default_headers={"User-Agent": self.UA, "Accept": "application/json"},
        )

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Zhihu for questions and articles. Requires login cookies.

        知乎已不再支持游客搜索（2026-07 实测返回 400）。
        需要提供 cookies (z_c0 + d_c0)。

        Args:
            keyword: Search query
            limit: Max results to return

        Returns:
            {"keyword": str, "items": [{title, excerpt, url, type, votes}]}
        """
        if not self.cookies:
            return {
                "error": "知乎搜索需要登录",
                "hint": "知乎已关闭游客搜索。请提供 cookies（z_c0 + d_c0）到 ~/.cn-scraper-cookies/zhihu.json",
            }

        enc = urllib.parse.quote(keyword)
        url = f"https://www.zhihu.com/api/v4/search_v3?q={enc}&type=content&limit={limit}&offset=0"

        headers = {}
        if self.cookies:
            headers["Cookie"] = self._cookie_str()

        status, data = self.http.get_json(url, headers=headers)

        if status == 0:
            return {"error": data.get("error", "搜索失败")}

        if status == 403:
            return {
                "error": "知乎搜索需要登录",
                "hint": "请提供知乎 cookies（z_c0 + d_c0）。\n"
                        "从浏览器 DevTools → Application → Cookies 导出。",
            }

        if status >= 400:
            return {"error": f"HTTP {status}: {data.get('error', 'Unknown error')}"}

        items = []
        for item in data.get("data", [])[:limit]:
            obj = item.get("object", {})
            items.append({
                "title": re.sub(r"<[^>]+>", "", str(obj.get("title") or obj.get("excerpt_title") or "")),
                "excerpt": re.sub(r"<[^>]+>", "", obj.get("excerpt", ""))[:200],
                "url": obj.get("url", ""),
                "type": obj.get("type", ""),
                "votes": obj.get("voteup_count", 0),
                "comments": obj.get("comment_count", 0),
                "id": obj.get("id", ""),
            })

        return {
            "keyword": keyword,
            "items": items,
        }

    def hot_list(self) -> dict:
        """Get current Zhihu hot list (trending topics). Requires login cookies.

        Returns:
            {"items": [{title, url,热度, excerpt}]}
        """
        if not self.cookies:
            return {
                "error": "知乎热榜需要登录",
                "hint": "请提供知乎 cookies（z_c0 + d_c0）到 ~/.cn-scraper-cookies/zhihu.json",
            }

        url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=20"
        headers = {}
        if self.cookies:
            headers["Cookie"] = self._cookie_str()

        status, data = self.http.get_json(url, headers=headers)

        if status == 0 or status >= 400:
            return {"error": data.get("error", f"HTTP {status}") if status else str(data)}

        items = []
        for item in data.get("data", []):
            target = item.get("target", {})
            items.append({
                "title": target.get("title", ""),
                "url": target.get("url", "").replace("api.zhihu.com", "www.zhihu.com"),
                "excerpt": target.get("excerpt", "")[:200],
                "hot_metric": target.get("metrics_area", {}).get("text", ""),
            })

        return {"items": items}
