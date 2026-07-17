"""Dianping (大众点评) engine using the public web pages."""

from __future__ import annotations

import html
import json
import re
import urllib.parse

from curl_cffi import requests

from cn_scraper_mcp.auth import CookieFileManager
from cn_scraper_mcp.http import HttpClient

_TAG_RE = re.compile(r"<[^>]+>")
_SHOP_RE = re.compile(r"/shop/([A-Za-z0-9]+)")


class DianpingEngine:
    """Read-only Dianping search, shop details, and reviews."""

    UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36"

    def __init__(self, cookies_path: str | None = None) -> None:
        manager = CookieFileManager("dianping", cookies_path=cookies_path)
        self.cookies = manager.load()
        self.http = HttpClient(
            timeout=15,
            max_retries=2,
            rate_limit_interval=1.0,
            default_headers={"User-Agent": self.UA, "Accept-Language": "zh-CN,zh;q=0.9"},
        )
        self.session = requests.Session(impersonate="chrome")

    def _headers(self) -> dict[str, str]:
        headers = {"Referer": "https://www.dianping.com/"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        return headers

    @staticmethod
    def _text(value: object, limit: int | None = None) -> str:
        text = html.unescape(_TAG_RE.sub("", str(value or ""))).strip()
        return text[:limit] if limit else text

    @staticmethod
    def _json_ld(page: str) -> dict:
        for raw in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            page,
            re.S | re.I,
        ):
            try:
                value = json.loads(html.unescape(raw.strip()))
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
        return {}

    def search(self, keyword: str, city: str = "", limit: int = 10) -> dict:
        encoded = urllib.parse.quote(keyword)
        url = f"https://www.dianping.com/search/keyword/1/0_{encoded}"
        status, page = self.http.get_text(url, session=self.session, headers=self._headers())
        if status != 200:
            return {
                "keyword": keyword,
                "city": city,
                "count": 0,
                "items": [],
                "error": f"HTTP {status}",
            }
        if any(marker in page.lower() for marker in ("captcha", "verify", "error code")):
            return {
                "keyword": keyword,
                "city": city,
                "count": 0,
                "items": [],
                "error": "大众点评页面触发风控",
                "hint": "请降低调用频率，必要时使用本地浏览器登录后重试。",
            }
        items = []
        seen: set[str] = set()
        for match in _SHOP_RE.finditer(page):
            shop_id = match.group(1)
            if shop_id in seen:
                continue
            seen.add(shop_id)
            window = page[max(0, match.start() - 500) : match.end() + 1000]
            title = re.search(r"<h4[^>]*>(.*?)</h4>", window, re.S | re.I)
            items.append(
                {
                    "id": shop_id,
                    "name": self._text(title.group(1) if title else ""),
                    "url": f"https://www.dianping.com/shop/{shop_id}",
                }
            )
            if len(items) >= limit:
                break
        return {"keyword": keyword, "city": city, "count": len(items), "items": items}

    def shop(self, shop_id: str) -> dict:
        status, page = self.http.get_text(
            f"https://www.dianping.com/shop/{shop_id}",
            session=self.session,
            headers=self._headers(),
        )
        if status != 200:
            return {"id": shop_id, "error": f"HTTP {status}"}
        if "error code" in page.lower() or "captcha" in page.lower():
            return {"id": shop_id, "error": "大众点评页面触发风控", "hint": "请稍后重试。"}
        data = self._json_ld(page)
        rating = data.get("aggregateRating") or {}
        return {
            "id": str(shop_id),
            "name": data.get("name", ""),
            "address": data.get("address", {}).get("streetAddress", "")
            if isinstance(data.get("address"), dict)
            else str(data.get("address", "")),
            "telephone": data.get("telephone", ""),
            "rating": rating.get("ratingValue", 0),
            "review_count": rating.get("reviewCount", 0),
            "url": f"https://www.dianping.com/shop/{shop_id}",
        }

    def reviews(self, shop_id: str, limit: int = 20) -> dict:
        status, page = self.http.get_text(
            f"https://www.dianping.com/shop/{shop_id}/review_all",
            session=self.session,
            headers=self._headers(),
        )
        if status != 200:
            return {"shop_id": shop_id, "count": 0, "reviews": [], "error": f"HTTP {status}"}
        if "error code" in page.lower() or "captcha" in page.lower():
            return {
                "shop_id": shop_id,
                "count": 0,
                "reviews": [],
                "error": "大众点评页面触发风控",
                "hint": "请稍后重试。",
            }
        reviews = []
        for block in re.findall(
            r'<div[^>]+class=["\'][^"\']*(?:review-words|review-item)[^"\']*["\'][^>]*>(.*?)</div>',
            page,
            re.S | re.I,
        ):
            text = self._text(block, 1000)
            if text:
                reviews.append({"content": text})
            if len(reviews) >= limit:
                break
        return {"shop_id": str(shop_id), "count": len(reviews), "reviews": reviews}
