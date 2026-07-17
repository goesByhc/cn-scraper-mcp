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
from cn_scraper_mcp.errors import AuthRequiredError, technical_error_from_http
from cn_scraper_mcp.http import HttpClient

_HTML_RE = re.compile(r"<[^>]+>")


def _clean_html(text: str) -> str:
    """Strip HTML tags from Zhihu comment content."""
    if not text:
        return ""
    return _HTML_RE.sub("", text).strip()


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
        # Zhihu prepends hot_timing and other metadata records. Request a
        # wider upstream window, then apply the caller's limit after filtering.
        fetch_limit = min(50, max(10, limit * 3))
        url = f"https://www.zhihu.com/api/v4/search_v3?q={enc}&type=content&limit={fetch_limit}&offset=0"

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
        for item in data.get("data", []):
            obj = item.get("object", {})
            content_type = obj.get("type", "")
            content_id = obj.get("id")
            if content_type not in {"answer", "question", "article"} or not content_id:
                continue
            items.append({
                "title": re.sub(r"<[^>]+>", "", str(obj.get("title") or obj.get("excerpt_title") or "")),
                "excerpt": re.sub(r"<[^>]+>", "", obj.get("excerpt", ""))[:200],
                "url": obj.get("url", ""),
                "type": content_type,
                "votes": obj.get("voteup_count", 0),
                "comments": obj.get("comment_count", 0),
                "id": content_id,
            })
            if len(items) >= limit:
                break

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

    def get_answer(self, answer_id: int | str) -> dict:
        """Get a single answer's detail (content, stats, comments count).

        Args:
            answer_id: Answer ID from search results (type="answer" items).

        Returns:
            {id, content, excerpt, author, votes, comments, question_title, url}
        """
        if not self.cookies:
            return {"error": "知乎需要登录", "hint": "请提供 cookies（z_c0 + d_c0）"}

        url = f"https://www.zhihu.com/api/v4/answers/{answer_id}?include=content,excerpt,voteup_count,comment_count"
        headers = {"Cookie": self._cookie_str()}
        status, data = self.http.get_json(url, headers=headers)

        if status == 0:
            return {"error": data.get("error", "获取回答失败"), "answer_id": str(answer_id)}
        if status == 403:
            return {"error": "知乎需要登录", "answer_id": str(answer_id), "hint": "请提供 cookies"}
        if status >= 400:
            return {"error": f"HTTP {status}", "answer_id": str(answer_id)}

        return {
            "id": data.get("id", answer_id),
            "content": data.get("content", ""),
            "excerpt": data.get("excerpt", ""),
            "author": (data.get("author", {}) or {}).get("name", ""),
            "votes": data.get("voteup_count", 0),
            "comments": data.get("comment_count", 0),
            "question_title": (data.get("question", {}) or {}).get("title", ""),
            "url": data.get("url", f"https://www.zhihu.com/answer/{answer_id}"),
        }

    def get_question_answers(self, question_id: int | str, limit: int = 20) -> dict:
        """Get answers for a Zhihu question.

        Args:
            question_id: Question ID from search results (type="question" items).
            limit: Max answers to return (default 20).

        Returns:
            {question_id, count, items: [{id, content, author, votes, comments}]}
        """
        if not self.cookies:
            return {"error": "知乎需要登录", "hint": "请提供 cookies"}

        url = f"https://www.zhihu.com/api/v4/questions/{question_id}/answers?limit={limit}&offset=0&include=content,excerpt,voteup_count,comment_count"
        headers = {"Cookie": self._cookie_str()}
        status, data = self.http.get_json(url, headers=headers)

        if status == 0:
            return {"error": data.get("error", "获取回答列表失败"), "question_id": str(question_id)}
        if status == 403:
            return {"error": "知乎需要登录", "question_id": str(question_id)}
        if status >= 400:
            return {"error": f"HTTP {status}", "question_id": str(question_id)}

        items = []
        for a in data.get("data", [])[:limit]:
            items.append({
                "id": a.get("id", ""),
                "content": re.sub(r"<[^>]+>", "", a.get("content", "") or ""),
                "excerpt": re.sub(r"<[^>]+>", "", a.get("excerpt", "") or "")[:200],
                "author": (a.get("author", {}) or {}).get("name", ""),
                "votes": a.get("voteup_count", 0),
                "comments": a.get("comment_count", 0),
            })

        return {
            "question_id": str(question_id),
            "count": len(items),
            "items": items,
        }

    def get_comments(self, answer_id: int | str, limit: int = 20, offset: int = 0) -> dict:
        """Get comments for a Zhihu answer (supports pagination via offset).

        Args:
            answer_id: Answer ID from search results (type="answer" items).
            limit: Max comments to return (default 20).
            offset: Pagination offset (0 = first page).

        Returns:
            {answer_id, count, comments: [{id, content, author, likes, time}], has_next}
        """
        if not self.cookies:
            raise AuthRequiredError(
                "知乎评论需要登录",
                hint="请提供知乎 cookies（z_c0 + d_c0）",
            )

        url = (
            f"https://www.zhihu.com/api/v4/answers/{answer_id}/comments"
            f"?order=normal&limit={limit}&offset={offset}"
        )
        headers = {"Cookie": self._cookie_str()}
        status, data = self.http.get_json(url, headers=headers)

        if status == 0 or status >= 400:
            raise technical_error_from_http("zhihu", status)

        comments = []
        for c in data.get("data", [])[:limit]:
            author = c.get("author", {}) or {}
            member = author.get("member", {}) or {}
            comments.append({
                "id": c.get("id", ""),
                "content": _clean_html(c.get("content", "")),
                "author": member.get("name", "") or author.get("name", ""),
                "likes": c.get("vote_count", 0),
                "time": c.get("created_time", 0),
            })

        return {
            "answer_id": str(answer_id),
            "count": len(comments),
            "comments": comments,
            "has_next": not data.get("paging", {}).get("is_end", False),
        }
