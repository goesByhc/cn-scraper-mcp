"""Zhihu (知乎) search engine.

知乎 has moderate anti-bot: guest search works with a mobile UA on curl,
but logged-in content and full articles require cookies.

Requirements (for full access):
    - Cookie file: $ZHIHU_COOKIES_FILE or ~/.ecom-cookies/zhihu.json
    - Key cookies: z_c0, d_c0 (auth)
"""

import json, os, urllib.parse, urllib.request, re
from pathlib import Path
from typing import Optional


class ZhihuEngine:
    """Search Zhihu (知乎) for content.

    Two modes:
    - Guest: curl with mobile UA (limited results, no logged-in content)
    - Logged-in: cookies from a browser session (full access)

    Usage:
        engine = ZhihuEngine(cookies_path="~/.ecom-cookies/zhihu.json")
        results = engine.search("半导体 投资", limit=10)
    """

    UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1")

    def __init__(self, cookies_path: Optional[str] = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "ZHIHU_COOKIES_FILE",
                str(Path.home() / ".ecom-cookies" / "zhihu.json"),
            )
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Zhihu for questions and articles.

        Uses the mobile API endpoint which is more lenient than desktop.

        Args:
            keyword: Search query
            limit: Max results to return

        Returns:
            {"keyword": str, "items": [{title, excerpt, url, type, votes}]}
        """
        enc = urllib.parse.quote(keyword)
        # zhihu mobile search API
        url = f"https://www.zhihu.com/api/v4/search_v3?q={enc}&type=content&limit={limit}&offset=0"

        headers = {
            "User-Agent": self.UA,
            "Accept": "application/json",
        }
        if self.cookies:
            headers["Cookie"] = self._cookie_str()

        req = urllib.request.Request(url, headers=headers)

        try:
            resp = urllib.request.urlopen(req, timeout=15)
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 403 and not self.cookies:
                return {
                    "error": "知乎搜索需要登录",
                    "hint": "请提供知乎 cookies（z_c0 + d_c0）。\n"
                            "从浏览器 DevTools → Application → Cookies 导出。",
                }
            return {"error": f"HTTP {e.code}: {e.reason}"}
        except Exception as e:
            return {"error": f"搜索失败: {e}"}

        items = []
        for item in data.get("data", [])[:limit]:
            obj = item.get("object", {})
            items.append({
                "title": re.sub(r"<[^>]+>", "", obj.get("title", obj.get("excerpt_title", ""))),
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
                "hint": "请提供知乎 cookies（z_c0 + d_c0）到 ~/.ecom-cookies/zhihu.json",
            }

        url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=20"
        headers = {"User-Agent": self.UA, "Cookie": self._cookie_str()} if self.cookies else {"User-Agent": self.UA}

        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": str(e)}

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
