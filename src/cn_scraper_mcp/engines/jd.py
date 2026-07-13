"""JD.com (京东) search engine via Chrome CDP.

京东 has the strictest anti-bot of the three platforms:
- Headless Chrome returns 0 results (must be HEADFUL)
- Cookie-injected new profile doesn't work — needs a persistent logged-in profile
- Old selectors (li.gl-item, #J_goodsList, .p-name, .p-price) are ALL DEAD as of 2026
- Current selector: div[data-sku] with hashed CSS class names
- Old APIs: p.3.cn/prices (DNS dead), club.jd.com/comment (返"系统繁忙") — DO NOT USE

Requirements:
    - Chrome installed (headful required)
    - ~/.jd_login_profile logged into jd.com at least once
"""

import json
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from .cdp import CDPClient, find_chrome, is_chrome_running, launch_chrome

# Default debug port for JD
JD_PORT = 9247


# ── Custom JD-specific exceptions ─────────────────────────────

class JDLoginWallError(Exception):
    """Page redirected to JD login — user must re-login in browser profile."""
    pass


class JDCaptchaError(Exception):
    """Captcha / verification wall detected — anti-bot trigger."""
    pass


class JDEmptyError(Exception):
    """Search returned zero results (genuine empty, not a block)."""
    pass


# ── JS extractor (runs in browser via CDP) ────────────────────

EXTRACT_JS = r"""
(function(){
  var items = [];
  var seenSkus = {};

  // ── Multi-selector fallback ──────────────────────
  var cards = document.querySelectorAll('div[data-sku]');
  if (!cards || cards.length === 0) {
    cards = document.querySelectorAll('div.gl-item');
  }
  if (!cards || cards.length === 0) {
    cards = document.querySelectorAll('div.goods-list-v2 > div');
  }

  cards.forEach(function(el){
    var sku = el.getAttribute('data-sku') || '';
    if (!sku) {
      // Try other attributes that might hold the SKU
      var skuEl = el.querySelector('[data-sku]');
      sku = skuEl ? skuEl.getAttribute('data-sku') : '';
    }

    // Dedup in-browser too (belt-and-suspenders)
    if (seenSkus[sku]) return;
    seenSkus[sku] = true;

    // ── Name extraction ────────────────────────────
    var img = el.querySelector('img');
    var name = (img && img.getAttribute('alt')) || '';
    if (!name) {
      var nameEl = el.querySelector('.p-name, .p-name-type-2, [class*="title"], [class*="name"]');
      name = nameEl ? nameEl.innerText.trim() : '';
    }

    // ── Price extraction: find ALL ¥ patterns ──────
    var allPrices = [];
    var spans = el.querySelectorAll('span, em, i, strong, div');
    spans.forEach(function(sp){
      var txt = sp.innerText || sp.textContent || '';
      var matches = txt.match(/[¥￥]\s*([\d,.]+)/g);
      if (matches) {
        matches.forEach(function(m){
          var num = parseFloat(m.replace(/[¥￥\s,]/g, ''));
          if (!isNaN(num) && num > 0) allPrices.push(num);
        });
      }
    });
    // Deduplicate prices and sort ascending
    allPrices = allPrices.filter(function(v, i, a){ return a.indexOf(v) === i; });
    allPrices.sort(function(a, b){ return a - b; });

    // ── Ad detection ────────────────────────────────
    var ad = el.innerText.indexOf('广告') >= 0;

    items.push({
      sku: sku,
      name: name,
      prices: allPrices,
      ad: ad
    });
  });

  // ── Page signals for state detection ─────────────
  var bodyText = (document.body && document.body.innerText || '').substring(0, 3000);
  var url = window.location.href;

  return JSON.stringify({
    count: items.length,
    items: items,
    url: url,
    pageText: bodyText
  });
})()
"""


# ── Engine ────────────────────────────────────────────────────


class JDEngine:
    """Search JD.com via headful Chrome CDP.

    Usage:
        engine = JDEngine(profile_dir="~/.jd_login_profile")
        engine.ensure_chrome()       # start Chrome if not running
        result = engine.search("京东京造沐光", limit=10)
        # result = {"keyword": ..., "count": ..., "items": [...]}
    """

    def __init__(self, profile_dir: Optional[str] = None, port: int = JD_PORT):
        if profile_dir is None:
            profile_dir = str(Path.home() / ".jd_login_profile")
        self.profile_dir = profile_dir
        self.port = port
        self._cdp: Optional[CDPClient] = None

    # ── Chrome lifecycle ───────────────────────────────────

    def ensure_chrome(self) -> bool:
        """Ensure Chrome is running on the JD debug port.

        If Chrome is already running → do nothing.
        If not → launch it (headful, with the persistent profile).

        Returns True if Chrome is now running.
        """
        if is_chrome_running(self.port):
            return True

        return launch_chrome(
            self.port,
            self.profile_dir,
            url="https://www.jd.com",
            headless=False,  # ⚠️ JD REQUIRES headful
        )

    # ── Core extraction logic (testable!) ─────────────────

    def _extract_products(
        self, raw_data: dict, page_url_override: Optional[str] = None
    ) -> dict:
        """Post-process raw CDP extraction data.

        Handles: dedup by SKU, price selection (lowest ¥ value),
        page state detection (login wall, captcha, empty).

        Args:
            raw_data: Dict from EXTRACT_JS evaluation, containing:
                - items: [{sku, name, prices: [float, ...], ad}]
                - url: page URL
                - pageText: first 3000 chars of body text
            page_url_override: Optional URL override for testing.

        Returns:
            {
                "state": "ok" | "login_wall" | "captcha" | "empty",
                "count": int,
                "items": [{sku, name, price, ad}],
                "error_code": str | None,
                "error_message": str | None,
            }
        """
        items_raw = raw_data.get("items", []) if raw_data else []
        page_url = page_url_override or raw_data.get("url", "") if raw_data else ""
        page_text = raw_data.get("pageText", "") if raw_data else ""

        # ── Page state detection ─────────────────────────
        state = self._detect_page_state(page_url, page_text, len(items_raw))
        if state != "ok":
            return {
                "state": state,
                "count": 0,
                "items": [],
                "error_code": self._state_error_code(state),
                "error_message": self._state_error_message(state),
            }

        # ── Deduplicate by SKU ───────────────────────────
        seen: Dict[str, dict] = {}
        for it in items_raw:
            sku = (it.get("sku") or "").strip()
            if not sku:
                continue
            if sku in seen:
                # Keep the one with more prices or first occurrence
                existing = seen[sku]
                if len(it.get("prices", [])) > len(existing.get("prices", [])):
                    seen[sku] = it
            else:
                seen[sku] = it

        # ── Pick best price (lowest ¥ value) ─────────────
        items_out = []
        for sku, it in seen.items():
            prices = it.get("prices", [])
            best_price = min(prices) if prices else None
            items_out.append({
                "sku": sku,
                "name": it.get("name", ""),
                "price": best_price,
                "ad": it.get("ad", False),
            })

        return {
            "state": "ok",
            "count": len(items_out),
            "items": items_out,
            "error_code": None,
            "error_message": None,
        }

    @staticmethod
    def _detect_page_state(url: str, page_text: str, item_count: int) -> str:
        """Detect what kind of page we're looking at.

        Returns one of: "ok", "login_wall", "captcha", "empty"
        """
        # ── Login wall detection ─────────────────────────
        login_signals = [
            "passport.jd.com",
            "reg.jd.com",
            "login.jd.com",
        ]
        if any(sig in url.lower() for sig in login_signals):
            return "login_wall"

        # Check page text for login indicators
        if page_text:
            login_text_signals = ["请登录", "账户登录", "扫码登录", "手机验证码登录"]
            if any(sig in page_text for sig in login_text_signals):
                return "login_wall"

        # ── Captcha / verification detection ─────────────
        captcha_signals = [
            "verify",
            "captcha",
            "验证码",
            "滑块验证",
            "人机验证",
            "京东安全",
            "jd_Safe",
        ]
        if any(sig.lower() in url.lower() for sig in captcha_signals[:2]):
            return "captcha"
        if page_text:
            if any(sig in page_text for sig in captcha_signals):
                return "captcha"

        # ── Normal empty ──────────────────────────────────
        if item_count == 0:
            return "empty"

        return "ok"

    @staticmethod
    def _state_error_code(state: str) -> str:
        """Map page state to error code."""
        codes = {
            "login_wall": "JD_LOGIN_REQUIRED",
            "captcha": "JD_CAPTCHA",
            "empty": "JD_EMPTY",
        }
        return codes.get(state, "JD_UNKNOWN")

    @staticmethod
    def _state_error_message(state: str) -> str:
        """Map page state to human-readable error message."""
        messages = {
            "login_wall": "京东页面跳转至登录页，请在Chrome中登录jd.com后重试",
            "captcha": "京东触发了验证码/风控，请稍后再试或切换网络环境",
            "empty": "搜索无结果（非风控/登录拦截）",
        }
        return messages.get(state, "未知页面状态")

    # ── Public API ────────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search JD for products. Launches Chrome if needed.

        Args:
            keyword: Search query
            limit: Max items to return

        Returns:
            {"keyword": str, "count": int, "items": [...]}
            Each item: {sku, name, price, ad}

        Raises:
            JDLoginWallError: Page redirected to login
            JDCaptchaError: Captcha/verification detected
            JDEmptyError: Zero results (genuine empty)
        """
        import asyncio

        if not self.ensure_chrome():
            return {
                "error": "无法启动京东浏览器",
                "hint": (
                    "请确保 Chrome 已安装。"
                    f"Profile 路径: {self.profile_dir}\n"
                    "首次使用需在弹窗的 Chrome 中登录 jd.com 一次。"
                ),
            }

        enc = urllib.parse.quote(keyword)
        search_url = (
            f"https://search.jd.com/Search?keyword={enc}&enc=utf-8"
        )

        async def _do_search():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect()
                await cdp.enable()
                await cdp.navigate(search_url, wait=6)
                # Run extractor
                raw = await cdp.evaluate(EXTRACT_JS, return_by_value=True)
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw or {"count": 0, "items": [], "url": "", "pageText": ""}
            finally:
                await cdp.close()

        try:
            raw_result = asyncio.run(_do_search())
        except Exception as e:
            return {"error": f"京东搜索异常: {e}"}

        # Post-process through the testable extraction function
        extracted = self._extract_products(raw_result)

        state = extracted["state"]

        if state == "login_wall":
            raise JDLoginWallError(extracted["error_message"])
        if state == "captcha":
            raise JDCaptchaError(extracted["error_message"])
        if state == "empty":
            # Return empty result — not an error, just nothing found
            return {
                "keyword": keyword,
                "count": 0,
                "items": [],
                "state": "empty",
            }

        # Normal results
        items = []
        for it in extracted["items"][:limit]:
            items.append({
                "sku": it.get("sku", ""),
                "name": it.get("name", ""),
                "price": it.get("price"),
                "ad": it.get("ad", False),
                "url": (
                    f"https://item.jd.com/{it.get('sku', '')}.html"
                    if it.get("sku")
                    else ""
                ),
            })

        return {
            "keyword": keyword,
            "count": extracted["count"],
            "items": items,
            "state": "ok",
        }

    def close_chrome(self):
        """Kill the JD Chrome process."""
        import subprocess

        subprocess.run(
            ["taskkill", "//F", "//IM", "chrome.exe"],
            capture_output=True,
            shell=True,  # cmd builtin on Windows
        )
