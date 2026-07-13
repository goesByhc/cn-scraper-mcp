"""ZSXQ (知识星球) content engine via REST API.

知识星球 is a Chinese paid-community platform (KOL subscription groups).
The v2 API is REST-based with cookie auth — NO browser needed.

Requirements:
    - Cookie file: $ZSXQ_COOKIES_FILE or ~/.ecom-cookies/zsxq.json
    - Key cookie: zsxq_access_token
    - Group ID: numeric, e.g. 15555442414282

Endpoints:
    GET /v2/groups/{id}/topics?count=N       — latest topics
    GET /v2/groups/{id}/topics?scope=by_owner — owner-only posts
"""

import json, os, urllib.request
from pathlib import Path
from typing import Optional


class ZsxqEngine:
    """Fetch and parse 知识星球 (ZSXQ) group content.

    Pure REST API — no browser, no CDP, just cookie auth.

    Usage:
        engine = ZsxqEngine(cookies_path="~/.ecom-cookies/zsxq.json")
        topics = engine.get_topics("28888555451", count=5)
        article = engine.get_article("https://articles.zsxq.com/id_xxx.html")
    """

    BASE = "https://api.zsxq.com/v2"

    def __init__(self, cookies_path: Optional[str] = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "ZSXQ_COOKIES_FILE",
                str(Path.home() / ".ecom-cookies" / "zsxq.json"),
            )
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))

    def _cookie_str(self) -> str:
        parts = []
        for k, v in self.cookies.items():
            parts.append(f"{k}={v}")
        parts.append("abtest_env=product")
        return "; ".join(parts)

    def _get(self, url: str) -> dict:
        """GET a ZSXQ API endpoint with cookie auth."""
        req = urllib.request.Request(url, headers={
            "Cookie": self._cookie_str(),
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return {"error": f"HTTP {e.code}", "succeeded": False}
        except Exception as e:
            return {"error": str(e), "succeeded": False}

    # ── topics ───────────────────────────────────────────

    def get_topics(self, group_id: str, count: int = 5, owner_only: bool = False) -> dict:
        """Fetch latest topics from a ZSXQ group.

        Args:
            group_id: Numeric group ID (e.g. "28888555451")
            count: Number of topics to fetch
            owner_only: If True, only group owner's posts (scope=by_owner)

        Returns:
            {"group_id": str, "topics": [{topic_id, title, text, author, created_at, comments}]}
        """
        scope = "by_owner" if owner_only else "all"
        url = f"{self.BASE}/groups/{group_id}/topics?scope={scope}&count={count}"
        data = self._get(url)

        if not data.get("succeeded", False):
            return {"error": data.get("error", "API 返回失败"), "group_id": group_id}

        topics = []
        for t in data.get("resp_data", {}).get("topics", []):
            talk = t.get("talk", {})
            owner = talk.get("owner", {})
            text = talk.get("text", "")

            # Check for article-type post
            article_url = None
            if talk.get("article"):
                article_url = talk["article"].get("inline_article_url")

            # Parse comments
            comments = []
            for c in t.get("show_comments", []):
                co = c.get("owner", {})
                comments.append({
                    "user": co.get("name", ""),
                    "text": c.get("text", "")[:300],
                    "likes": c.get("likes_count", 0),
                    "time": c.get("create_time", ""),
                })

            topics.append({
                "topic_id": str(t.get("topic_id", "")),
                "title": t.get("title", ""),
                "text": text[:500] if text else "",
                "author": owner.get("name", ""),
                "author_id": str(owner.get("user_id", "")),
                "created_at": t.get("create_time", ""),
                "likes": t.get("likes_count", 0),
                "comments_count": t.get("comments_count", 0),
                "readers": t.get("readers_count", 0),
                "is_article": bool(talk.get("article")),
                "article_url": article_url,
                "has_images": bool(talk.get("images")),
                "comments": comments,
            })

        return {
            "group_id": group_id,
            "count": len(topics),
            "topics": topics,
        }

    # ── article ──────────────────────────────────────────

    def get_article(self, article_url: str) -> dict:
        """Fetch the full body of an article-type ZSXQ post.

        Article posts only show a preview in talk.text.
        The full content is at talk.article.inline_article_url.

        Args:
            article_url: The inline_article_url from a topic (e.g. "https://articles.zsxq.com/id_xxx.html")

        Returns:
            {"url": str, "text": str}
        """
        req = urllib.request.Request(article_url, headers={
            "Cookie": self._cookie_str(),
            "User-Agent": "Mozilla/5.0",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            return {"error": str(e), "url": article_url}

        # Extract content from known ZSXQ article HTML containers
        import re
        # Try ql-editor first, then tiptap-preview
        for pattern in [
            r'<div[^>]*class="[^"]*content ql-editor[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*tiptap-preview[^"]*"[^>]*>(.*?)</div>',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                text = re.sub(r"<[^>]+>", "", m.group(1))  # strip HTML tags
                text = re.sub(r"\s+", " ", text).strip()
                return {"url": article_url, "text": text}

        return {"url": article_url, "text": "", "error": "无法从 HTML 提取文章内容"}
