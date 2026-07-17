"""Bilibili (哔哩哔哩) public video APIs.

The current search, popular, view, and top-level reply endpoints work without
cookies or a browser.  Keep this engine HTTP-only until Bilibili explicitly
requires a signed or logged-in request; do not add browser automation merely
to make it resemble another video platform.
"""

from __future__ import annotations

import html
import re
from typing import Any

from curl_cffi import requests

from cn_scraper_mcp.errors import PlatformError, RateLimitError, technical_error_from_http
from cn_scraper_mcp.http import HttpClient

_HTML_RE = re.compile(r"<[^>]+>")


class BilibiliRiskControlError(RateLimitError):
    """Bilibili JSON risk-control code (-352/-412)."""


class BilibiliEngine:
    """Access Bilibili's platform-native public video capabilities."""

    API_BASE = "https://api.bilibili.com"
    UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    )

    def __init__(self) -> None:
        self.session = requests.Session(impersonate="chrome")
        self.http = HttpClient(
            timeout=15,
            max_retries=2,
            backoff_base=1.0,
            rate_limit_interval=0.5,
            default_headers={
                "Accept": "application/json",
                "Referer": "https://www.bilibili.com/",
                "User-Agent": self.UA,
            },
        )

    @staticmethod
    def _text(value: object) -> str:
        return html.unescape(_HTML_RE.sub("", str(value or ""))).strip()

    @staticmethod
    def _image_url(value: object) -> str:
        url = str(value or "")
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("http://"):
            return f"https://{url.removeprefix('http://')}"
        return url

    def _get(
        self,
        path: str,
        params: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict:
        status, payload = self.http.get_json(
            f"{self.API_BASE}{path}",
            params=params,
            headers=headers,
            session=self.session,
        )
        if status in {403, 429}:
            raise RateLimitError(
                f"Bilibili rejected the request with HTTP {status}",
                hint="Wait before retrying and reduce request frequency.",
            )
        if status != 200:
            raise technical_error_from_http("Bilibili", status)

        if not isinstance(payload, dict):
            raise PlatformError("Bilibili API returned a non-object JSON response")

        code = payload.get("code")
        if code in {-352, -412}:
            raise BilibiliRiskControlError(
                "Bilibili rejected the request as abnormal",
                hint="Bilibili risk control was triggered. Wait before retrying and reduce request frequency.",
            )
        if code != 0:
            message = self._text(payload.get("message")) or "unknown API error"
            raise PlatformError(
                f"Bilibili API returned code {code}: {message}",
                retryable=code not in {-400, -404},
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformError("Bilibili API returned an invalid data object")
        return data

    @classmethod
    def _video_item(cls, item: dict[str, Any]) -> dict:
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
        bvid = str(item.get("bvid") or "")
        author = item.get("author") or owner.get("name") or ""
        author_id = item.get("mid") or owner.get("mid") or ""
        return {
            "bvid": bvid,
            "aid": str(item.get("aid") or ""),
            "title": cls._text(item.get("title")),
            "description": cls._text(item.get("description") or item.get("desc")),
            "author": cls._text(author),
            "author_id": str(author_id),
            "duration": item.get("duration", 0),
            "views": item.get("play", stat.get("view", 0)),
            "danmaku": item.get("video_review", stat.get("danmaku", 0)),
            "comments": stat.get("reply", 0),
            "likes": stat.get("like", 0),
            "published_at": item.get("pubdate", 0),
            "cover": cls._image_url(item.get("pic")),
            "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        }

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Bilibili videos without login or a browser."""
        data = self._get(
            "/x/web-interface/search/type",
            {"search_type": "video", "keyword": keyword, "page": "1"},
        )
        raw_items = data.get("result")
        items = [
            self._video_item(item)
            for item in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(item, dict) and item.get("bvid")
        ][:limit]
        return {
            "keyword": keyword,
            "total": data.get("numResults", len(items)),
            "count": len(items),
            "items": items,
        }

    def popular(self, limit: int = 20) -> dict:
        """Get Bilibili's current popular-video list."""
        data = self._get(
            "/x/web-interface/popular",
            {"pn": "1", "ps": str(limit)},
        )
        raw_items = data.get("list")
        items = [
            self._video_item(item)
            for item in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(item, dict) and item.get("bvid")
        ][:limit]
        return {"count": len(items), "items": items, "no_more": bool(data.get("no_more"))}

    def get_video(self, bvid: str) -> dict:
        """Get one video's metadata and engagement statistics."""
        data = self._get("/x/web-interface/view", {"bvid": bvid})
        item = self._video_item(data)
        stat = data.get("stat") if isinstance(data.get("stat"), dict) else {}
        item.update({
            "favorites": stat.get("favorite", 0),
            "coins": stat.get("coin", 0),
            "shares": stat.get("share", 0),
            "pages": data.get("pages", []),
        })
        return item

    def get_comments(self, bvid: str, limit: int = 20, cursor: str = "") -> dict:
        """Get top-level video comments using Bilibili's numeric cursor."""
        view = self._get("/x/web-interface/view", {"bvid": bvid})
        aid = str(view.get("aid") or "")
        if not aid:
            raise PlatformError("Bilibili video response did not include aid")

        referer = {"Referer": f"https://www.bilibili.com/video/{bvid}"}
        legacy_page = False
        try:
            data = self._get(
                "/x/v2/reply/main",
                {
                    "type": "1",
                    "oid": aid,
                    "mode": "3",
                    "next": cursor or "0",
                    "ps": str(limit),
                },
                headers=referer,
            )
        except BilibiliRiskControlError:
            # reply/main intermittently returns -352 for otherwise valid guest
            # traffic.  The older public endpoint remains available and uses
            # a numeric page cursor, which fits the same MCP continuation field.
            legacy_page = True
            data = self._get(
                "/x/v2/reply",
                {
                    "type": "1",
                    "oid": aid,
                    "pn": cursor or "1",
                    "ps": str(limit),
                    "sort": "2",
                },
                headers=referer,
            )
        raw_replies = data.get("replies")
        comments = []
        for reply in (raw_replies if isinstance(raw_replies, list) else [])[:limit]:
            if not isinstance(reply, dict):
                continue
            member = reply.get("member") if isinstance(reply.get("member"), dict) else {}
            content = reply.get("content") if isinstance(reply.get("content"), dict) else {}
            comments.append({
                "id": str(reply.get("rpid_str") or reply.get("rpid") or ""),
                "content": self._text(content.get("message")),
                "user": self._text(member.get("uname")),
                "user_id": str(member.get("mid") or ""),
                "likes": reply.get("like", 0),
                "reply_count": reply.get("rcount", 0),
                "time": reply.get("ctime", 0),
            })

        if legacy_page:
            page = data.get("page") if isinstance(data.get("page"), dict) else {}
            page_num = int(page.get("num") or cursor or 1)
            page_size = int(page.get("size") or limit)
            total = int(page.get("count") or len(comments))
            is_end = page_num * page_size >= total or not comments
            next_cursor = "" if is_end else str(page_num + 1)
            pagination = "page"
        else:
            cursor_data = data.get("cursor") if isinstance(data.get("cursor"), dict) else {}
            total = cursor_data.get("all_count", len(comments))
            is_end = bool(cursor_data.get("is_end"))
            next_cursor = "" if is_end else str(cursor_data.get("next") or "")
            pagination = "cursor"
        return {
            "bvid": bvid,
            "aid": aid,
            "total": total,
            "count": len(comments),
            "comments": comments,
            "next_cursor": next_cursor,
            "is_end": is_end,
            "pagination": pagination,
        }
