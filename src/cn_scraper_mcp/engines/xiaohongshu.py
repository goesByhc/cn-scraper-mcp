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
    - Cookie file: $XHS_COOKIES_FILE or ~/.cn-scraper-cookies/xiaohongshu.json

Page state detection:
    - Login expired: URL contains 'login'/'passport', or '登录' in page text
    - IP risk: page text contains error_code=300012 or 'IP存在风险'
    - Captcha: page text contains verification/slider prompts ('验证', '滑块')

Comments:
    - Only first-screen comments are fetched (not paginated).
"""

import json, os, asyncio, urllib.parse, re
from pathlib import Path
from typing import Optional

from .cdp import (
    CDPClient, is_chrome_running, launch_chrome,
    find_obscura, launch_obscura, close_browser,
)

XHS_PORT = 9251
OBSCURA_PORT = 9222  # Obscura's fixed CDP port

# ── Error codes ──────────────────────────────────────────────────────

ERR_LOGIN_EXPIRED = "XHS_LOGIN_EXPIRED"
ERR_IP_RISK = "XHS_IP_RISK"
ERR_CAPTCHA = "XHS_CAPTCHA"
ERR_NOTE_NOT_FOUND = "XHS_NOTE_NOT_FOUND"

# ── JS extractors ────────────────────────────────────────────────────

SEARCH_EXTRACTOR = r"""
(function(){
  var result = {
    url: window.location.href,
    pageText: document.body ? (document.body.innerText || '').substring(0, 3000) : '',
    items: []
  };

  // Multi-selector fallback — try several note-card selectors
  var selectors = [
    'section.note-item',
    'div.note-item',
    'div[class*="note"] a[href*="/explore/"]',
    'a[href*="/explore/"][href*="xsec_token"]'
  ];

  var seen = {};
  var rawItems = [];

  for (var si = 0; si < selectors.length; si++) {
    if (rawItems.length > 0) break;  // stop once we have results
    var els = document.querySelectorAll(selectors[si]);
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var link;

      if (el.tagName === 'A') {
        link = el;
      } else {
        link = el.querySelector('a[href*="/explore/"]');
      }
      if (!link) continue;

      var href = link.href;
      if (!href || seen[href]) continue;
      seen[href] = true;

      var m = href.match(/(?:search_result|explore)\/([0-9a-f]{16,})/);
      var noteId = m ? m[1] : '';

      // xsec_token from href query string
      var xsec = '';
      try {
        var u = new URL(href);
        xsec = u.searchParams.get('xsec_token') || '';
      } catch(e) {}

      var container = el.tagName === 'A' ? el.parentElement : el;

      // Title — try multiple possible selectors
      var titleEl = container.querySelector('.title, .note-title, [class*="title"]');
      var title = titleEl ? titleEl.innerText.trim() : '';

      // Author
      var authorEl = container.querySelector('.author .name, .author-name, .nickname, [class*="author"] [class*="name"]');
      var author = authorEl ? authorEl.innerText.trim() : '';

      // Likes
      var likesEl = container.querySelector('.like-wrapper .count, .like-count, [class*="like"] [class*="count"], .count');
      var likes = likesEl ? likesEl.innerText.trim() : '0';

      rawItems.push({
        title: title,
        author: author,
        likes: likes,
        noteId: noteId,
        href: href,
        xsec_token: xsec
      });
    }
  }

  result.items = rawItems;
  return JSON.stringify(result);
})()
"""

# Template for note detail — note_id is interpolated via format()
NOTE_DETAIL_EXTRACTOR_TEMPLATE = r"""
(function(){
  try {{
    var state = window.__INITIAL_STATE__;
    if (!state || !state.note) return JSON.stringify({{error: 'no __INITIAL_STATE__'}});

    var detail = state.note.noteDetailMap || {{}};
    var noteId = '{note_id}';

    // STRICT indexing: use noteDetailMap[noteId] — NEVER Object.values()[0]
    var entry = detail[noteId];

    // Fallback: search by noteId field inside note objects (for key mismatches)
    if (!entry) {{
      var keys = Object.keys(detail);
      for (var i = 0; i < keys.length; i++) {{
        var val = detail[keys[i]];
        if (val && val.note && val.note.noteId === noteId) {{
          entry = val;
          break;
        }}
      }}
    }}

    if (!entry || !entry.note) {{
      return JSON.stringify({{
        error: 'note_id not found in noteDetailMap',
        requested_note_id: noteId,
        available_ids: Object.keys(detail)
      }});
    }}

    var n = entry.note;
    return JSON.stringify({{
      id: n.noteId,
      title: n.title,
      desc: n.desc,
      type: n.type,
      likes: (n.interactInfo || {{}}).likedCount,
      collects: (n.interactInfo || {{}}).collectedCount,
      comments: (n.interactInfo || {{}}).commentCount,
      user: {{name: (n.user || {{}}).nickname, id: (n.user || {{}}).userId}},
      tags: (n.tagList || []).map(function(t){{return t.name;}}),
      time: n.time
    }});
  }} catch(e) {{ return JSON.stringify({{error: e.message}}); }}
}})()
"""

# Template for comments — note_id is interpolated; only first-screen comments
COMMENT_EXTRACTOR_TEMPLATE = r"""
(function(){
  try {{
    var state = window.__INITIAL_STATE__;
    if (!state || !state.note) return JSON.stringify([]);

    var detail = state.note.noteDetailMap || {{}};
    var noteId = '{note_id}';

    var entry = detail[noteId];
    if (!entry) {{
      var keys = Object.keys(detail);
      for (var i = 0; i < keys.length; i++) {{
        var val = detail[keys[i]];
        if (val && val.note && val.note.noteId === noteId) {{
          entry = val;
          break;
        }}
      }}
    }}

    if (!entry || !entry.note) return JSON.stringify([]);

    // Only first-screen comments (state.note.noteDetailMap[id].note.comments.list)
    var comments = (entry.note.comments || {{}}).list || [];
    return JSON.stringify(comments.map(function(c){{
      return {{
        content: c.content,
        userName: (c.userInfo || {{}}).nickname,
        likes: c.likeCount,
        time: c.createTime
      }};
    }}));
  }} catch(e) {{ return JSON.stringify([]); }}
}})()
"""


# ── Like count standardization ────────────────────────────────────────

_LIKE_RE = re.compile(r'^([\d.]+)\s*(万|w)?$', re.IGNORECASE)


def _standardize_likes(raw: str) -> int:
    """Standardize XHS like-count strings to integers.

    Rules:
        '1.2万' / '1.2w' → 12000
        '999+'          → 999
        '' / None       → 0
        '2300'          → 2300
    """
    if not raw or not isinstance(raw, str):
        return 0

    raw = raw.strip()
    if not raw:
        return 0

    # '999+' → strip trailing +
    if raw.endswith('+'):
        raw = raw[:-1].strip()

    # Match: number [optional 万/w]
    m = _LIKE_RE.match(raw)
    if m:
        num = float(m.group(1))
        unit = m.group(2)
        if unit:  # 万 or w
            return int(num * 10000)
        return int(num)

    # Last resort: try pure float
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return 0


# ── Page state detection ──────────────────────────────────────────────

def _detect_page_state(url: str, page_text: str, item_count: int) -> tuple:
    """Detect which state the XHS page is in.

    Returns (state, error_code, error_message) where state is one of:
        - 'ok': normal results
        - 'login_expired': login session expired
        - 'ip_risk': IP flagged (error_code=300012)
        - 'captcha': verification / slider challenge
        - 'empty': no results, no block signals

    Priority: IP risk > login expired > captcha > empty/ok
    """
    low_url = url.lower()
    low_text = page_text.lower() if page_text else ""

    # 1. IP risk — most severe (even valid cookies won't help)
    if 'error_code=300012' in low_url or 'error_code=300012' in low_text or \
       'ip存在风险' in low_text or 'ip 存在风险' in low_text:
        return (
            'ip_risk',
            ERR_IP_RISK,
            'IP 被小红书标记为风险（error_code=300012），需要用住宅 IP 的本地浏览器访问。'
        )

    # 2. Login expired — session/cookie issue
    if 'login' in low_url or 'passport' in low_url:
        return (
            'login_expired',
            ERR_LOGIN_EXPIRED,
            '登录已过期，需要重新登录小红书。请更新 cookies 文件。'
        )
    if '登录' in page_text and 'passport' not in low_url:
        return (
            'login_expired',
            ERR_LOGIN_EXPIRED,
            '检测到登录页面，cookies 可能已过期。请更新 ~/.cn-scraper-cookies/xiaohongshu.json'
        )

    # 3. Captcha / verification
    captcha_keywords = ['验证', '滑块', '验证码', '人机验证', '请完成验证', '滑动验证']
    if any(kw in page_text for kw in captcha_keywords):
        return (
            'captcha',
            ERR_CAPTCHA,
            '小红书弹出验证码/滑块验证。请在浏览器中手动完成验证后重试。'
        )

    # 4. Empty or OK
    if item_count == 0:
        return (
            'empty',
            'XHS_EMPTY',
            '未找到相关笔记。非风控问题，可能是关键词无结果。'
        )

    return ('ok', None, None)


# ── Engine ────────────────────────────────────────────────────────────

class XiaohongshuEngine:
    """Search and read Xiaohongshu (小红书) notes via local browser CDP.

    Prefers Obscura (lightweight, built-in anti-detection, ~30MB RAM).
    Falls back to Chrome if Obscura not installed.

    Usage:
        engine = XiaohongshuEngine(cookies_path="~/.cn-scraper-cookies/xiaohongshu.json")
        engine.ensure_browser()
        results = engine.search("儿童学习桌", limit=10)
        note = engine.get_note(results["items"][0]["noteId"])
        comments = engine.get_comments(results["items"][0]["noteId"])
    """

    def __init__(self, cookies_path: Optional[str] = None, port: int = XHS_PORT):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "XHS_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "xiaohongshu.json")
        self.cookies_path = cookies_path
        self.port = port

        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))
        else:
            self.cookies = {}

    # ── Browser lifecycle (Obscura preferred, Chrome fallback) ──────

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

    # ── search ─────────────────────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Xiaohongshu for notes.

        Args:
            keyword: Search query
            limit: Max notes to return

        Returns:
            {"keyword": str, "state": str, "count": int, "items": [...],
             "error_code": str|None, "error_message": str|None}
            Each item: {title, author, likes, noteId, href, xsec_token}
        """
        if not self.ensure_browser():
            return {
                "keyword": keyword,
                "state": "error",
                "count": 0,
                "items": [],
                "error_code": "XHS_BROWSER_UNAVAILABLE",
                "error_message": (
                    "无法启动浏览器。XHS 需要本地浏览器（不能用云浏览器——数据中心 IP 会被封）。"
                    "推荐安装 Obscura（轻量+内置反检测），或使用 Chrome。"
                ),
            }

        enc = urllib.parse.quote(keyword)
        search_url = (
            f"https://www.xiaohongshu.com/search_result"
            f"?keyword={enc}&source=web_explore_feed&type=51"
        )

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp.enable()
                await cdp.navigate(search_url, wait=5)
                raw = await cdp.evaluate(SEARCH_EXTRACTOR, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "{}")
            finally:
                await cdp.close()

        try:
            raw_result = asyncio.run(_do())
        except Exception as e:
            return {
                "keyword": keyword,
                "state": "error",
                "count": 0,
                "items": [],
                "error_code": "XHS_SEARCH_EXCEPTION",
                "error_message": f"小红书搜索异常: {e}",
            }

        return self._parse_search(keyword, raw_result, limit)

    def _parse_search(self, keyword: str, raw: dict, limit: int) -> dict:
        """Parse raw JS extraction result into structured search output.

        Handles: page state detection, like count standardization,
        xsec_token preservation, limit truncation.
        """
        url = raw.get("url", "")
        page_text = raw.get("pageText", "")
        raw_items = raw.get("items", [])

        # Standardize items
        items = []
        for r in raw_items:
            note_id = r.get("noteId", "")
            if not note_id:
                continue  # skip items without noteId

            items.append({
                "title": r.get("title", ""),
                "author": r.get("author", ""),
                "likes": _standardize_likes(r.get("likes", "0")),
                "noteId": note_id,
                "href": r.get("href", ""),
                "xsec_token": r.get("xsec_token", ""),
            })

        # Detect page state
        state, error_code, error_message = _detect_page_state(url, page_text, len(items))

        return {
            "keyword": keyword,
            "state": state,
            "count": len(items[:limit]),
            "items": items[:limit],
            "error_code": error_code,
            "error_message": error_message,
        }

    # ── note detail ──────────────────────────────────────────────────

    def get_note(self, note_id: str) -> dict:
        """Get full note detail (title, body, likes, tags, user).

        STRICTLY indexes noteDetailMap[note_id] — NEVER uses Object.values()[0].

        Args:
            note_id: Note ID (from search result)

        Returns:
            Note detail dict with {id, title, desc, likes, comments, tags, user, time}
            On error: {error, requested_note_id, ...}
        """
        if not self.ensure_browser():
            return {"error": ERR_LOGIN_EXPIRED, "error_message": "浏览器不可用"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        extractor = NOTE_DETAIL_EXTRACTOR_TEMPLATE.replace('{note_id}', note_id)

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp._send("Page.enable")
                await cdp._send("Runtime.enable")
                await cdp.navigate(url, wait=4)
                raw = await cdp.evaluate(extractor, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "{}")
            finally:
                await cdp.close()

        try:
            result = asyncio.run(_do())
        except Exception as e:
            return {"error": str(e), "requested_note_id": note_id}

        return result

    # ── comments ─────────────────────────────────────────────────────

    def get_comments(self, note_id: str) -> dict:
        """Get comments for a note.

        IMPORTANT: Only first-screen comments are fetched (not paginated).
        Xiaohongshu loads additional comment pages via XHR — this method
        captures only the comments rendered in the initial page state
        (usually 10-20 top-level comments).

        Args:
            note_id: Note ID

        Returns:
            {"noteId": str, "comments": [{content, userName, likes, time}]}
        """
        if not self.ensure_browser():
            return {"error": ERR_LOGIN_EXPIRED, "error_message": "浏览器不可用"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        extractor = COMMENT_EXTRACTOR_TEMPLATE.replace('{note_id}', note_id)

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp._send("Page.enable")
                await cdp._send("Runtime.enable")
                await cdp.navigate(url, wait=4)
                raw = await cdp.evaluate(extractor, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "[]")
            finally:
                await cdp.close()

        try:
            comments = asyncio.run(_do())
        except Exception as e:
            return {"noteId": note_id, "comments": [], "error": str(e)}

        return {"noteId": note_id, "comments": comments}

    # ── cleanup ──────────────────────────────────────────

    def cleanup(self):
        """Terminate ONLY the browser process we launched.

        Uses cdp.close_browser() which terminates only our managed
        process — never touches the user's personal Chrome or other
        browser instances.
        """
        close_browser(self.port)
        if self.port != OBSCURA_PORT:
            close_browser(OBSCURA_PORT)
