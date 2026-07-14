"""Douyin (抖音) engine — hot list + search.

Hot list: ✅ Works with login cookies (aweme/v1/web/hot/search/list/)
Search:   ❌ Requires X-Gorgon/X-Khronos signed headers (aweme_list always null)
"""

import json, os, urllib.parse
from pathlib import Path
from typing import Optional

from cn_scraper_mcp.http import HttpClient


class DouyinEngine:
    """Douyin (抖音) — hot list and search.

    Hot list works with login cookies (sessionid + others).
    Search API requires encrypted signatures — not feasible for programmatic access.

    Usage:
        engine = DouyinEngine(cookies_path="~/.cn-scraper-cookies/douyin.json")
        hot = engine.hot_list()       # ✅ works
        results = engine.search("...")  # ❌ returns error with alternatives
    """

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

    SEARCH_URL = "https://www.douyin.com/aweme/v1/web/search/item/"
    HOT_LIST_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"

    def __init__(self, cookies_path: Optional[str] = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "DOUYIN_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "douyin.json")
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))

        self.http = HttpClient(
            default_headers={
                "User-Agent": self.UA,
                "Referer": "https://www.douyin.com/",
            },
            max_retries=2,
        )

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # ── hot list ───────────────────────────────────────────

    def hot_list(self) -> dict:
        """Get Douyin trending search list. Requires login cookies.

        Returns:
            {"count": int, "items": [{word, hot_value, position, label}]}
        """
        if not self.cookies:
            return {
                "error": "抖音热搜需要登录",
                "hint": "请用 guided_login('douyin') 登录后收割 cookie。",
            }

        headers = {"Cookie": self._cookie_str()}
        status, data = self.http.get_json(self.HOT_LIST_URL, headers=headers)

        if status == 0:
            return {"error": data.get("error", "请求失败")}
        if status >= 400:
            return {"error": f"HTTP {status}"}

        word_list = data.get("data", {}).get("word_list", [])
        items = []
        for w in word_list:
            info = w.get("word_record", w.get("sentence_info", w))
            items.append({
                "word": info.get("word", "") or w.get("word", ""),
                "hot_value": info.get("hot_value", 0),
                "position": w.get("position", 0),
                "label": f"热{w.get('position',0)}" if w.get("position") else "",
            })

        return {"count": len(items), "items": items}

    # ── search (returns honest error) ─────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Douyin — ⚠️ 当前不可用。

        抖音搜索 API 需要 X-Gorgon/X-Khronos/X-Argus 加密签名头。
        即使使用登录 cookie，aweme_list 也始终返回 null。
        """
        return {
            "keyword": keyword,
            "error": "抖音搜索需要加密签名（X-Gorgon/X-Khronos/X-Argus）",
            "status": "UNSUPPORTED",
            "hint": (
                "抖音搜索 API 在服务端验证签名——即使有登录 cookie 也返回空结果。\n"
                "可用功能: douyin_hot_list（热搜榜）✅\n"
                "替代方案: 飞瓜数据、蝉妈妈（第三方付费服务）、抖音开放平台（企业资质）"
            ),
        }
