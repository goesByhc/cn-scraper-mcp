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

import asyncio
import json
import re
import urllib.parse
from pathlib import Path

from cn_scraper_mcp.auth import CookieFileManager

from .cdp import (
    CDPClient,
    close_browser,
    get_browser_lock,
    is_chrome_running,
    launch_chrome,
    launch_obscura,
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

  // Strategy: find note-card links by href pattern.  xsec_token in the
  // query string is the most reliable signal — search-result cards carry
  // it, plain /explore/ links and related-search tags do not.
  var noteIdRe = /\/(explore|search_result|discovery\/item)\/([0-9a-f]{16,})/;
  var seen = {};
  var rawItems = [];
  var links = document.querySelectorAll('a[href*="xsec_token"]');

  for (var i = 0; i < links.length; i++) {
    var link = links[i];
    var href = link.getAttribute('href') || '';
    var m = href.match(noteIdRe);
    if (!m) continue;
    var noteId = m[2];
    if (seen[noteId]) continue;
    seen[noteId] = true;

    var xsec = '';
    try {
      var u = new URL(href, window.location.origin);
      xsec = u.searchParams.get('xsec_token') || '';
    } catch(e) {}

    // ── Title: try aria-label on link, then full-text of link, then
    //        the nearest .title / [class*="title"] ancestor ──
    var title = (link.getAttribute('aria-label') || '').trim();
    if (!title) title = link.innerText.trim();
    if (!title) {
      // Walk up and look for a title element
      var p = link;
      for (var d = 0; d < 6; d++) {
        if (!p) break;
        var t = p.querySelector('.title, [class*="title"]');
        if (t) { title = t.innerText.trim(); break; }
        p = p.parentElement;
      }
    }
    // Title may be very long — truncate
    if (title && title.length > 200) title = title.substring(0, 200);

    // ── Author: walk up, then scan for author/name elements ──
    var author = '';
    var p2 = link;
    for (var d = 0; d < 6; d++) {
      if (!p2) break;
      var a = p2.querySelector('.name, .author, .nickname, [class*="author"], [class*="AccountName"]');
      if (a) { author = a.innerText.trim(); break; }
      p2 = p2.parentElement;
    }
    // Strip trailing timestamp (e.g. "世界杯瞬间\n9小时前" → "世界杯瞬间")
    if (author) author = author.split('\n').slice(0, 1).join('');

    // ── Likes: walk up, then scan for like/count elements ──
    // XHS DOM is CSS-module-heavy — try multiple fallback selectors.
    var likes = '0';
    var likeSelectors = [
      '[class*="like"] span', '[class*="Like"] span', '[class*="count"]',
      '[class*="Count"]', '[class*="interact"] span', 'footer span',
      '[class*="footer"] span', '[class*="action"] span', 'span[class*="num"]'
    ];
    var p3 = link;
    for (var d = 0; d < 6; d++) {
      if (!p3) break;
      for (var si = 0; si < likeSelectors.length; si++) {
        var els = p3.querySelectorAll(likeSelectors[si]);
        for (var ei = 0; ei < els.length; ei++) {
          var t = els[ei].innerText.trim();
          // Must contain a digit to be a plausible like count
          if (/[0-9]/.test(t)) { likes = t; break; }
        }
        if (likes !== '0') break;
      }
      if (likes !== '0') break;
      p3 = p3.parentElement;
    }

    rawItems.push({
      title: title,
      author: author,
      likes: likes,
      noteId: noteId,
      href: href,
      xsec_token: xsec
    });
  }

  result.items = rawItems;
  return JSON.stringify(result);
})()
"""

# Template for note detail — __NOTE_ID__ is interpolated via str.replace.
NOTE_DETAIL_EXTRACTOR_TEMPLATE = r"""
(function(){
  try {
    var state = window.__INITIAL_STATE__;
    if (!state || !state.note) return JSON.stringify({error: 'no __INITIAL_STATE__'});

    var detail = state.note.noteDetailMap || {};
    var noteId = '__NOTE_ID__';

    // STRICT indexing: use noteDetailMap[noteId] — NEVER Object.values()[0]
    var entry = detail[noteId];

    // Fallback: search by noteId field inside note objects (for key mismatches)
    if (!entry) {
      var keys = Object.keys(detail);
      for (var i = 0; i < keys.length; i++) {
        var val = detail[keys[i]];
        if (val && val.note && val.note.noteId === noteId) {
          entry = val;
          break;
        }
      }
    }

    if (!entry || !entry.note) {
      return JSON.stringify({
        error: 'note_id not found in noteDetailMap',
        requested_note_id: noteId,
        available_ids: Object.keys(detail)
      });
    }

    var n = entry.note;
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

# Template for comments — __NOTE_ID__ is interpolated via str.replace.
# Prefer structured initial state, then fall back to the rendered comment DOM.
COMMENT_EXTRACTOR_TEMPLATE = r"""
(function(){
  try {
    var noteId = '__NOTE_ID__';
    var result = {
      url: window.location.href,
      pageText: document.body ? (document.body.innerText || '').substring(0, 3000) : '',
      source: 'none',
      comments: []
    };
    var seen = {};

    function firstValue(obj, keys) {
      for (var i = 0; i < keys.length; i++) {
        var value = obj && obj[keys[i]];
        if (value !== undefined && value !== null && value !== '') return value;
      }
      return '';
    }

    function nodeText(root, selectors) {
      for (var i = 0; i < selectors.length; i++) {
        var node = root.querySelector(selectors[i]);
        if (node && node.innerText && node.innerText.trim()) return node.innerText.trim();
      }
      return '';
    }

    function addComment(raw) {
      if (!raw) return;
      var user = raw.userInfo || raw.user || raw.author || {};
      var content = firstValue(raw, ['content', 'text', 'commentContent']);
      var userName = firstValue(user, ['nickname', 'name', 'userName']) ||
                     firstValue(raw, ['userName', 'nickname']);
      if (!content) return;
      var id = firstValue(raw, ['id', 'commentId', 'comment_id']);
      var key = id || (userName + '\n' + content);
      if (seen[key]) return;
      seen[key] = true;
      result.comments.push({
        id: id,
        content: String(content).trim(),
        userName: String(userName || '').trim(),
        userId: firstValue(user, ['userId', 'user_id', 'id']),
        likes: firstValue(raw, ['likeCount', 'like_count', 'likedCount', 'likes']),
        time: firstValue(raw, ['createTime', 'create_time', 'time', 'date'])
      });
    }

    // Current and older web clients have used several shapes for the
    // first page of comments.  Inspect only the requested note entry.
    var state = window.__INITIAL_STATE__;
    var detail = state && state.note && state.note.noteDetailMap || {};
    var entry = detail[noteId];
    if (!entry) {
      var keys = Object.keys(detail);
      for (var k = 0; k < keys.length; k++) {
        var candidate = detail[keys[k]];
        if (candidate && candidate.note && candidate.note.noteId === noteId) {
          entry = candidate;
          break;
        }
      }
    }
    var stateLists = entry ? [
      entry.commentList,
      entry.comments && entry.comments.list,
      entry.note && entry.note.commentList,
      entry.note && entry.note.comments && entry.note.comments.list,
      entry.commentData && entry.commentData.comments,
      entry.commentData && entry.commentData.commentList
    ] : [];
    for (var s = 0; s < stateLists.length; s++) {
      if (!Array.isArray(stateLists[s])) continue;
      for (var c = 0; c < stateLists[s].length; c++) addComment(stateLists[s][c]);
    }
    if (result.comments.length) result.source = 'initial_state';

    // XHS normally renders comments after its signed XHR finishes.  CSS
    // class names vary, so use stable semantic fragments with exact
    // selectors first and normalize the result into one public shape.
    if (!result.comments.length) {
      var nodes = document.querySelectorAll(
        '.comment-item, [data-comment-id], [class*="comment-item"], [class*="commentItem"]'
      );
      for (var n = 0; n < nodes.length; n++) {
        var root = nodes[n];
        var content = nodeText(root, [
          '.content .note-text', '.comment-content', '[class*="comment-content"]',
          '[class*="commentContent"]', '.content'
        ]);
        if (!content) continue;
        var id = root.getAttribute('data-comment-id') || root.getAttribute('data-id') || '';
        var userName = nodeText(root, [
          '.author-wrapper .author', '.author .name', '.author', '.nickname',
          '[class*="author"] [class*="name"]', '[class*="nickname"]'
        ]);
        var likes = nodeText(root, [
          '.like-wrapper .count', '.like .count', '.like-count',
          '[class*="like"] [class*="count"]'
        ]);
        var time = nodeText(root, [
          '.date', '.time', '[class*="comment-time"]', '[class*="commentTime"]'
        ]);
        addComment({id: id, content: content, userName: userName, likes: likes, time: time});
      }
      if (result.comments.length) result.source = 'dom';
    }

    return JSON.stringify(result);
  } catch(e) {
    return JSON.stringify({
      url: window.location.href,
      pageText: document.body ? (document.body.innerText || '').substring(0, 3000) : '',
      source: 'error',
      comments: [],
      extractorError: e.message
    });
  }
})()
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
        comments = engine.get_comments(
            results["items"][0]["noteId"],
            xsec_token=results["items"][0]["xsec_token"],
        )
    """

    def __init__(self, cookies_path: str | None = None, port: int = XHS_PORT):
        mgr = CookieFileManager("xiaohongshu", cookies_path=cookies_path)
        self.cookies = mgr.load()
        self.port = port

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
            with get_browser_lock(self.port):
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

    def get_note(self, note_id: str, xsec_token: str | None = None) -> dict:
        """Get full note detail (title, body, likes, tags, user).

        STRICTLY indexes noteDetailMap[note_id] — NEVER uses Object.values()[0].

        Args:
            note_id: Note ID (from search result)
            xsec_token: Access token from the matching search result.

        Returns:
            Note detail dict with {id, title, desc, likes, comments, tags, user, time}
            On error: {error, requested_note_id, ...}
        """
        if not self.ensure_browser():
            return {"error": ERR_LOGIN_EXPIRED, "error_message": "浏览器不可用"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if xsec_token:
            query = urllib.parse.urlencode({
                "xsec_token": xsec_token,
                "xsec_source": "pc_search",
            })
            url = f"{url}?{query}"
        extractor = NOTE_DETAIL_EXTRACTOR_TEMPLATE.replace('__NOTE_ID__', note_id)

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
            with get_browser_lock(self.port):
                result = asyncio.run(_do())
        except Exception as e:
            return {"error": str(e), "requested_note_id": note_id}

        return result

    # ── comments ─────────────────────────────────────────────────────

    def get_comments(self, note_id: str, xsec_token: str | None = None) -> dict:
        """Get comments for a note.

        IMPORTANT: Only first-screen comments are fetched (not paginated).
        Xiaohongshu loads additional comment pages via XHR — this method
        captures only the comments rendered in the initial page state
        (usually 10-20 top-level comments).

        Args:
            note_id: Note ID
            xsec_token: Access token from the matching search result.  Current
                XHS note pages require this token to load comment data reliably.

        Returns:
            {"noteId": str, "comments": [{content, userName, likes, time}]}
        """
        if not self.ensure_browser():
            return {"error": ERR_LOGIN_EXPIRED, "error_message": "浏览器不可用"}

        url = f"https://www.xiaohongshu.com/explore/{note_id}"
        if xsec_token:
            query = urllib.parse.urlencode({
                "xsec_token": xsec_token,
                "xsec_source": "pc_search",
            })
            url = f"{url}?{query}"
        extractor = COMMENT_EXTRACTOR_TEMPLATE.replace('__NOTE_ID__', note_id)

        async def _do():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect(url_hint="xiaohongshu.com")
                await cdp.enable()
                await cdp.navigate(url, wait=4)
                raw = await cdp.evaluate(extractor, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "{}")
            finally:
                await cdp.close()

        try:
            with get_browser_lock(self.port):
                raw_result = asyncio.run(_do())
        except Exception as e:
            return {"noteId": note_id, "count": 0, "comments": [], "error": str(e)}

        comments = []
        for raw_comment in raw_result.get("comments", []):
            if not isinstance(raw_comment, dict) or not raw_comment.get("content"):
                continue
            comments.append({
                "id": raw_comment.get("id", ""),
                "content": str(raw_comment.get("content", "")).strip(),
                "userName": str(raw_comment.get("userName", "")).strip(),
                "userId": raw_comment.get("userId", ""),
                "likes": _standardize_likes(raw_comment.get("likes", 0)),
                "time": raw_comment.get("time", ""),
            })

        state, error_code, error_message = _detect_page_state(
            raw_result.get("url", url),
            raw_result.get("pageText", ""),
            max(len(comments), 1),
        )
        result = {
            "noteId": note_id,
            "state": state,
            "count": len(comments),
            "comments": comments,
            "source": raw_result.get("source", "none"),
            "error_code": error_code,
            "error_message": error_message,
        }
        if raw_result.get("extractorError"):
            result["extractor_error"] = raw_result["extractorError"]

        return result

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
