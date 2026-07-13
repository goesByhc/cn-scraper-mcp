"""Xiaohongshu (小红书) search engine via local Chrome CDP.

小红书 has the strictest IP blocking of all Chinese platforms:
- Cloud/datacenter IP → error_code=300012 "IP存在风险" (rejected BEFORE checking cookies)
- Guest curl → ~9KB shell titled "你访问的页面不见了", empty __INITIAL_STATE__
- Results arrive via signed XHR (x-s/x-t headers), NOT server-side rendered

The ONLY reliable path: drive the user's LOCAL Chrome (residential IP)
with their XHS login cookies injected via CDP.

Requirements:
    - Chrome installed (local, NOT cloud browser)
    - XHS login cookies: web_session, a1, webId, gid, abRequestId, xsecappid, webBuild
    - Cookie file: $XHS_COOKIES_FILE or ~/.ecom-cookies/xiaohongshu.json
"""

import json, os, asyncio, urllib.parse
from pathlib import Path
from typing import Optional

from .cdp import (
    CDPClient, is_chrome_running, launch_chrome,
    find_obscura, launch_obscura,
)

XHS_PORT = 9251
OBSCURA_PORT = 9222  # Obscura's fixed CDP port

# ── JS extractors ───────────────────────────────────────────

SEARCH_EXTRACTOR = r"""
(function(){
  var notes = document.querySelectorAll('section.note-item');
  var result = [];
  notes.forEach(function(el){
    var title = (el.querySelector('.title') || {}).innerText || '';
    var author = (el.querySelector('.author .name') || {}).innerText || '';
    var likes = (el.querySelector('.like-wrapper .count') || {}).innerText || '0';
    var link = el.querySelector('a[href*="xsec_token"]');
    var href = link ? link.href : '';
    var m = href.match(/(?:search_result|explore)\/([0-9a-f]+)/);
    var noteId = m ? m[1] : '';
    result.push({title: title, author: author, likes: likes, noteId: noteId, href: href});
  });
  return JSON.stringify(result);
})()
"""

NOTE_DETAIL_EXTRACTOR = r"""
(function(){
  try {
    var state = window.__INITIAL_STATE__;
    if (!state || !state.note) return JSON.stringify({error: 'no __INITIAL_STATE__'});
    var detail = state.note.noteDetailMap || {};
    var first = Object.values(detail)[0];
    if (!first || !first.note) return JSON.stringify({error: 'no note in noteDetailMap'});
    var n = first.note;
    return JSON.stringify({
      id: n.noteId,
      title: n.title,
      desc: n.desc,
      type: n.type,
      likes: (n.interactInfo || {}).likedCount,
      collects: (n.interactInfo || {}).collectedCount,
      comments: (n.interactInfo || {}).commentCount,
      user: {name: (n.user || {}).nickname, id: (n.user || {}).userId},
      tags: (n.tagList || []).map(function(t){return t.name;}),
      time: n.time
    });
  } catch(e) { return JSON.stringify({error: e.message}); }
})()
"""

COMMENT_EXTRACTOR = r"""
(function(){
  try {
    var state = window.__INITIAL_STATE__;
    if (!state || !state.note) return JSON.stringify([]);
    var detail = state.note.noteDetailMap || {};
    var first = Object.values(detail)[0];
    if (!first || !first.note) return JSON.stringify([]);
    var comments = (first.note.comments || {}).list || [];
    return JSON.stringify(comments.map(function(c){
      return {
        content: c.content,
        userName: (c.userInfo || {}).nickname,
        likes: c.likeCount,
        time: c.createTime
      };
    }));
  } catch(e) { return JSON.stringify([]); }
})()
"""


class XiaohongshuEngine:
    """Search and read Xiaohongshu (小红书) notes via local browser CDP.

    Prefers Obscura (lightweight, built-in anti-detection, ~30MB RAM).
    Falls back to Chrome if Obscura not installed.

    Usage:
        engine = XiaohongshuEngine(cookies_path="~/.ecom-cookies/xiaohongshu.json")
        engine.ensure_browser()
        results = engine.search("儿童学习桌", limit=10)
        note = engine.get_note(results["items"][0]["noteId"])
        comments = engine.get_comments(results["items"][0]["noteId"])
    """

    def __init__(self, cookies_path: Optional[str] = None, port: int = XHS_PORT):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "XHS_COOKIES_FILE",
                str(Path.home() / ".ecom-cookies" / "xiaohongshu.json"),
            )
        self.cookies_path = cookies_path
        self.port = port

        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))
        else:
            self.cookies = {}

    # ── Browser lifecycle (Obscura preferred, Chrome fallback) ──

    def ensure_browser(self) -> bool:
        """Ensure a browser is running with XHS cookies injected.

        Tries Obscura first (--stealth, built-in anti-detection, ~30MB).
        Falls back to Chrome headful if Obscura not available.
        """
        # Already running?
        if is_chrome_running(self.port):
            return True
        if is_chrome_running(OBSCURA_PORT):
            self.port = OBSCURA_PORT
            return True

        # Try Obscura (lighter, anti-detection, XHS-friendly)
        try:
            launch_obscura(port=self.port, stealth=True)
            if is_chrome_running(self.port):
                self._inject_cookies()
                return True
        except FileNotFoundError:
            pass  # Obscura not installed, fall through to Chrome

        # Fallback: Chrome headful
        profile = str(Path.home() / ".xhs_cdp_profile")
        ok = launch_chrome(
            self.port, profile,
            url="https://www.xiaohongshu.com",
            headless=False,
        )
        if not ok:
            return False
        if self.cookies:
            self._inject_cookies()
        return True

    def _inject_cookies(self):
        """Inject XHS cookies into the running Chrome via CDP."""
        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect()
                await cdp._send("Network.enable")
                for name, value in self.cookies.items():
                    await cdp._send("Network.setCookie", {
                        "name": name, "value": str(value),
                        "domain": ".xiaohongshu.com", "path": "/",
                    })
            finally:
                await cdp.close()

        asyncio.run(_do())

    # ── search ───────────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Xiaohongshu for notes.

        Args:
            keyword: Search query
            limit: Max notes to return

        Returns:
            {"keyword": str, "items": [{title, author, likes, noteId, href}]}
        """
        if not self.ensure_browser():
            return {
                "error": "无法启动浏览器",
                "hint": ("XHS 需要本地浏览器（不能用云浏览器——数据中心 IP 会被封）。"
                         "推荐安装 Obscura（轻量+内置反检测），或使用 Chrome。"),
            }

        enc = urllib.parse.quote(keyword)
        search_url = f"https://www.xiaohongshu.com/search_result?keyword={enc}&source=web_explore_feed&type=51"

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp.enable()
                await cdp.navigate(search_url, wait=5)
                raw = await cdp.evaluate(SEARCH_EXTRACTOR, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "[]")
            finally:
                await cdp.close()

        try:
            items = asyncio.run(_do())
        except Exception as e:
            return {"error": f"小红书搜索异常: {e}"}

        return {
            "keyword": keyword,
            "items": items[:limit],
        }

    # ── note detail ──────────────────────────────────────

    def get_note(self, note_id: str) -> dict:
        """Get full note detail (title, body, likes, tags, user).

        Args:
            note_id: Note ID (from search result)

        Returns:
            Note detail dict with {id, title, desc, likes, comments, tags, user, time}
        """
        if not self.ensure_browser():
            return {"error": "Chrome not available"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp._send("Page.enable")
                await cdp._send("Runtime.enable")
                await cdp.navigate(url, wait=4)
                raw = await cdp.evaluate(NOTE_DETAIL_EXTRACTOR, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "{}")
            finally:
                await cdp.close()

        try:
            return asyncio.run(_do())
        except Exception as e:
            return {"error": str(e)}

    # ── comments ─────────────────────────────────────────

    def get_comments(self, note_id: str) -> dict:
        """Get comments for a note.

        Args:
            note_id: Note ID

        Returns:
            {"noteId": str, "comments": [{content, userName, likes, time}]}
        """
        if not self.ensure_browser():
            return {"error": "Chrome not available"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp._send("Page.enable")
                await cdp._send("Runtime.enable")
                await cdp.navigate(url, wait=4)
                raw = await cdp.evaluate(COMMENT_EXTRACTOR, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "[]")
            finally:
                await cdp.close()

        try:
            comments = asyncio.run(_do())
        except Exception as e:
            return {"noteId": note_id, "comments": [], "error": str(e)}

        return {"noteId": note_id, "comments": comments}
