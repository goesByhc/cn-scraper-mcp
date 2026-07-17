"""Douban (豆瓣) platform engine.

The engine talks to Douban's mobile JSON endpoints.  It intentionally keeps
search, subject details, and reviews as Douban-specific capabilities; callers
decide when each operation is useful.
"""

from __future__ import annotations

import html
import re
import urllib.parse

from curl_cffi import requests

from cn_scraper_mcp.auth import CookieFileManager
from cn_scraper_mcp.http import HttpClient

_TAG_RE = re.compile(r"<[^>]+>")


class DoubanEngine:
    """Access public Douban subjects and reviews."""

    BASE = "https://m.douban.com/rexxar/api/v2"
    UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 "
        "Mobile/15E148 Safari/604.1"
    )

    def __init__(self, cookies_path: str | None = None) -> None:
        manager = CookieFileManager("douban", cookies_path=cookies_path)
        self.cookies = manager.load()
        self.http = HttpClient(
            timeout=15,
            max_retries=2,
            rate_limit_interval=0.5,
            default_headers={"User-Agent": self.UA, "Accept": "application/json"},
        )
        # Douban rejects the stdlib TLS fingerprint for several mobile JSON
        # endpoints; keep the browser-like session local to this engine.
        self.session = requests.Session(impersonate="safari_ios")

    def _headers(self, referer: str = "https://m.douban.com/") -> dict[str, str]:
        headers = {"Referer": referer}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        return headers

    @staticmethod
    def _text(value: object, limit: int | None = None) -> str:
        text = html.unescape(_TAG_RE.sub("", str(value or ""))).strip()
        return text[:limit] if limit else text

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search subjects across Douban's supported content types."""
        status, data = self.http.get_json(
            f"{self.BASE}/search",
            params={"q": keyword, "start": "0", "count": str(min(limit, 50))},
            session=self.session,
            headers=self._headers(),
        )
        if status != 200:
            # The mobile search page remains available when the JSON endpoint
            # is disabled for a session.  Keep this fallback platform-local.
            page_status, page = self.http.get_text(
                "https://m.douban.com/search/",
                params={"query": keyword},
                session=self.session,
                headers=self._headers(),
            )
            if page_status != 200 or "search_results_subjects" not in page:
                return {"keyword": keyword, "count": 0, "items": [], "error": f"HTTP {status}"}
            items = []
            for match in re.finditer(
                r'<a href="/(?:movie|book|music)/subject/(\d+)/"[^>]*>.*?'
                r'<span class="subject-title">(.*?)</span>',
                page,
                re.S,
            ):
                subject_id, title = match.groups()
                items.append(
                    {
                        "id": subject_id,
                        "title": self._text(title),
                        "type": "",
                        "url": f"https://www.douban.com/subject/{subject_id}/",
                    }
                )
                if len(items) >= limit:
                    break
            return {"keyword": keyword, "count": len(items), "items": items}

        raw_items = data.get("items") or data.get("subjects") or data.get("data") or []
        items = []
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            subject_id = str(item.get("id") or item.get("subject_id") or "")
            if not subject_id:
                continue
            items.append(
                {
                    "id": subject_id,
                    "title": self._text(item.get("title") or item.get("name")),
                    "type": item.get("type", ""),
                    "year": str(item.get("year") or ""),
                    "rating": (item.get("rating") or {}).get("value", item.get("rating", 0)),
                    "url": item.get("url") or f"https://douban.com/subject/{subject_id}/",
                    "cover": item.get("pic", {}).get("normal", "")
                    if isinstance(item.get("pic"), dict)
                    else item.get("cover", ""),
                }
            )
        return {"keyword": keyword, "count": len(items), "items": items}

    def subject(self, subject_id: str) -> dict:
        """Get one Douban subject's metadata and summary."""
        status, data = self.http.get_json(
            f"{self.BASE}/subject/{urllib.parse.quote(subject_id)}",
            session=self.session,
            headers=self._headers(f"https://m.douban.com/subject/{subject_id}/"),
        )
        if status != 200:
            return {
                "id": subject_id,
                "error": f"HTTP {status}",
                "hint": "豆瓣条目详情接口可能需要登录态，请使用本地浏览器登录后重试。",
            }
        rating = data.get("rating") or {}
        return {
            "id": str(data.get("id", subject_id)),
            "title": self._text(data.get("title") or data.get("name")),
            "type": data.get("type", ""),
            "summary": self._text(data.get("summary")),
            "genres": data.get("genres", []),
            "year": str(data.get("year") or ""),
            "rating": rating.get("value", 0),
            "rating_count": rating.get("count", 0),
            "url": data.get("url") or f"https://douban.com/subject/{subject_id}/",
        }

    def reviews(self, subject_id: str, limit: int = 20, start: int = 0) -> dict:
        """Get short reviews for a subject."""
        status, data = self.http.get_json(
            f"{self.BASE}/subject/{urllib.parse.quote(subject_id)}/reviews",
            params={"count": str(limit), "start": str(start)},
            session=self.session,
            headers=self._headers(f"https://m.douban.com/subject/{subject_id}/"),
        )
        if status != 200:
            return {"subject_id": subject_id, "count": 0, "reviews": [], "error": f"HTTP {status}"}
        reviews = []
        for item in (data.get("reviews") or data.get("data") or [])[:limit]:
            author = item.get("author") or {}
            reviews.append(
                {
                    "id": str(item.get("id", "")),
                    "title": self._text(item.get("title")),
                    "content": self._text(item.get("summary") or item.get("content")),
                    "author": author.get("name", "") if isinstance(author, dict) else str(author),
                    "rating": (item.get("rating") or {}).get("value", item.get("rating", 0)),
                    "useful_count": item.get("useful_count", 0),
                    "created_at": item.get("created_at", ""),
                }
            )
        return {
            "subject_id": str(subject_id),
            "count": len(reviews),
            "reviews": reviews,
            "start": start,
        }
