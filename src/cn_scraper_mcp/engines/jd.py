"""JD.com (京东) engine using browser-signed APIs via Chrome CDP.

京东 has the strictest anti-bot of the three platforms:
- Headless Chrome returns 0 results (must be HEADFUL)
- Cookie-injected new profile doesn't work — needs a persistent logged-in profile
- Old selectors (li.gl-item, #J_goodsList, .p-name, .p-price) are ALL DEAD as of 2026
- Current selector: div[data-sku] with hashed CSS class names
- Product data is parsed from JD's current signed JSON APIs; DOM parsing is a fallback
- Old APIs: p.3.cn/prices (DNS dead), club.jd.com/comment (返"系统繁忙") — DO NOT USE

Requirements:
    - Chrome installed (headful required)
    - ~/.jd_login_profile logged into jd.com at least once
"""

import html
import json
import re
import urllib.parse
from pathlib import Path

from .cdp import CDPClient, close_browser, get_browser_lock, is_chrome_running, launch_chrome

# Default debug port for JD
JD_PORT = 9247


# Installed before JD's own scripts.  JD still creates and signs every request;
# this observer only copies the two public JSON responses used by the engine.
# It never records URLs, headers, cookies, request bodies, or signature values.
API_CAPTURE_JS = r"""
(() => {
  window.__JD_CAPTURE__ = Object.create(null);
  const allowed = new Set([
    'pc_search_searchWare',
    'pc_detailpage_wareBusiness'
  ]);
  const keep = (url, text) => {
    try {
      const functionId = new URL(url, location.href).searchParams.get('functionId');
      if (!allowed.has(functionId)) return;
      const value = JSON.parse(text);
      if (value && typeof value === 'object') {
        window.__JD_CAPTURE__[functionId] = value;
      }
    } catch (_) {}
  };

  const nativeOpen = XMLHttpRequest.prototype.open;
  const nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__jdCaptureUrl = String(url || '');
    return nativeOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    this.addEventListener('load', () => {
      keep(this.responseURL || this.__jdCaptureUrl, this.responseText || '');
    });
    return nativeSend.apply(this, arguments);
  };

  const nativeFetch = window.fetch;
  if (nativeFetch) {
    window.fetch = function() {
      return nativeFetch.apply(this, arguments).then(response => {
        response.clone().text()
          .then(text => keep(response.url, text))
          .catch(() => {});
        return response;
      });
    };
  }
})();
"""


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


# ── JS extractor for product detail ────────────────────────

PRODUCT_EXTRACT_JS = r"""
(function(){
  function findName() {
    var sel = document.querySelector('.sku-name');
    if (sel && sel.innerText.trim().length > 3) return sel.innerText.trim();
    // JD product titles are usually in the page <title> as "【商品名】价格图片..."
    var title = document.querySelector('title');
    if (title) {
      var t = title.innerText.trim();
      // Strip JD suffixes: "【价格", "图片", "京东", etc
      t = t.replace(/【[^】]*?(?:价格|图片|参数|评价)[^】]*?】/g, '');
      t = t.replace(/[-—–]\s*京东.*$/, '');
      t = t.trim();
      if (t.length > 3) return t;
    }
    return '';
  }
  return JSON.stringify({
    name: findName(),
    price: (function(){
      var el = document.querySelector('.p-price span') || document.querySelector('[class*="price"] span') || document.querySelector('.price');
      return el ? el.innerText.trim() : '';
    })(),
    shop: (function(){
      var el = document.querySelector('.J-hove-wrap .name a') || document.querySelector('[class*="shop"] a') || document.querySelector('[class*="seller"]');
      return el ? el.innerText.trim() : '';
    })(),
    specs: (function(){
      var items = document.querySelectorAll('.parameter2 li, .Ptable-item, [class*="parameter"]');
      var texts = [];
      for (var i = 0; i < items.length; i++) { texts.push(items[i].innerText.trim()); }
      return texts.join('; ');
    })(),
    url: window.location.href,
    pageText: document.body ? (document.body.innerText || '').substring(0, 2000) : ''
  });
})()
"""

# ── JS extractor for search (runs in browser via CDP) ──────

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

    def __init__(self, profile_dir: str | None = None, port: int = JD_PORT):
        if profile_dir is None:
            profile_dir = str(Path.home() / ".jd_login_profile")
        self.profile_dir = profile_dir
        self.port = port
        self._cdp: CDPClient | None = None

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

    @staticmethod
    def _plain_text(value: object) -> str:
        """Remove search-highlight HTML from a public JD text field."""
        without_tags = re.sub(r"<[^>]+>", "", str(value or ""))
        return html.unescape(without_tags).strip()

    @staticmethod
    def _is_ad(value: object) -> bool:
        return str(value or "").strip().lower() not in {"", "0", "false", "none"}

    @classmethod
    def _extract_search_api(cls, payload: dict) -> list[dict] | None:
        """Normalize ``pc_search_searchWare``; return None if invalid."""
        data = payload.get("data")
        if str(payload.get("code")) != "0" or not isinstance(data, dict):
            return None
        wares = data.get("wareList")
        if not isinstance(wares, list):
            return None

        items: list[dict] = []
        seen: set[str] = set()
        for ware in wares:
            if not isinstance(ware, dict):
                continue
            sku = str(ware.get("skuId") or ware.get("wareId") or "").strip()
            if not sku or sku in seen:
                continue
            seen.add(sku)
            price = None
            for field in ("realPrice", "jdPrice", "jdPriceText", "oriPrice"):
                try:
                    candidate = float(str(ware.get(field, "")).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if candidate > 0:
                    price = candidate
                    break
            items.append({
                "sku": sku,
                "name": cls._plain_text(ware.get("wareName")),
                "price": price,
                "ad": cls._is_ad(ware.get("isAdv")),
                "url": f"https://item.jd.com/{sku}.html",
            })
        return items

    @classmethod
    def _extract_product_api(cls, payload: dict, sku: str) -> dict | None:
        """Normalize ``pc_detailpage_wareBusiness``; return None if invalid."""
        head = payload.get("skuHeadVO")
        if not isinstance(head, dict) or not head.get("skuTitle"):
            return None

        price_data = payload.get("price")
        price = ""
        if isinstance(price_data, dict):
            final = price_data.get("finalPrice")
            if isinstance(final, dict):
                price = str(final.get("price") or "")
            if not price:
                price = str(price_data.get("p") or price_data.get("op") or "")

        shop_data = payload.get("itemShopInfo")
        shop = shop_data.get("shopName", "") if isinstance(shop_data, dict) else ""

        spec_parts: list[str] = []
        attributes = payload.get("productAttributeVO")
        if isinstance(attributes, dict):
            rows = attributes.get("attributes")
            core_rows = attributes.get("coreAttributes")
            all_rows = (rows if isinstance(rows, list) else []) + (
                core_rows if isinstance(core_rows, list) else []
            )
            for row in all_rows:
                if not isinstance(row, dict):
                    continue
                label = cls._plain_text(row.get("labelName"))
                value = cls._plain_text(row.get("labelValue"))
                if label and value:
                    spec_parts.append(f"{label}: {value}")

        return {
            "sku": sku,
            "name": cls._plain_text(head.get("skuTitle")),
            "price": price,
            "shop": cls._plain_text(shop),
            "specs": "; ".join(spec_parts),
            "url": f"https://item.jd.com/{sku}.html",
        }

    def _extract_products(
        self, raw_data: dict, page_url_override: str | None = None
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
        seen: dict[str, dict] = {}
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

        enc = urllib.parse.quote(keyword)
        search_url = (
            f"https://search.jd.com/Search?keyword={enc}&enc=utf-8"
        )

        async def _do_search():
            cdp = CDPClient(self.port)
            script_id = ""
            try:
                await cdp.connect()
                await cdp.enable()
                script_id = await cdp.add_script_on_new_document(API_CAPTURE_JS)
                await cdp.navigate(search_url, wait=5)
                api = await cdp.evaluate(
                    "window.__JD_CAPTURE__ && "
                    "window.__JD_CAPTURE__.pc_search_searchWare"
                )
                if isinstance(api, dict):
                    items = self._extract_search_api(api)
                    if items is not None:
                        return {"source": "api", "items": items}
                # Run extractor
                raw = await cdp.evaluate(EXTRACT_JS, return_by_value=True)
                if isinstance(raw, str):
                    raw = json.loads(raw)
                return {"source": "dom", "raw": raw or {}}
            finally:
                try:
                    await cdp.remove_script_on_new_document(script_id)
                finally:
                    await cdp.close()

        try:
            with get_browser_lock(self.port):
                if not self.ensure_chrome():
                    return {
                        "error": "无法启动京东浏览器",
                        "hint": (
                            "请确保 Chrome 已安装。"
                            f"Profile 路径: {self.profile_dir}\n"
                            "首次使用需在弹窗的 Chrome 中登录 jd.com 一次。"
                        ),
                    }
                raw_result = asyncio.run(_do_search())
        except Exception as e:
            return {"error": f"京东搜索异常: {e}"}

        if raw_result.get("source") == "api":
            api_items = raw_result.get("items", [])
            return {
                "keyword": keyword,
                "count": len(api_items),
                "items": api_items[:limit],
                "state": "ok" if api_items else "empty",
            }

        # Post-process through the DOM compatibility path.
        raw_payload = raw_result.get("raw", raw_result)
        extracted = self._extract_products(raw_payload)

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

    def get_product(self, sku: str) -> dict:
        """Get JD product detail via CDP.

        Args:
            sku: Product SKU from search results.

        Returns:
            {sku, name, price, shop, specs, url}
        """
        import asyncio

        product_url = f"https://item.jd.com/{sku}.html"

        async def _do():
            cdp = CDPClient(self.port)
            script_id = ""
            try:
                await cdp.connect()
                await cdp.enable()
                script_id = await cdp.add_script_on_new_document(API_CAPTURE_JS)
                await cdp.navigate(product_url, wait=5)
                api = await cdp.evaluate(
                    "window.__JD_CAPTURE__ && "
                    "window.__JD_CAPTURE__.pc_detailpage_wareBusiness"
                )
                if isinstance(api, dict):
                    product = self._extract_product_api(api, sku)
                    if product:
                        return product
                raw = await cdp.evaluate(PRODUCT_EXTRACT_JS, return_by_value=True)
                return json.loads(raw if isinstance(raw, str) else "{}")
            finally:
                try:
                    await cdp.remove_script_on_new_document(script_id)
                finally:
                    await cdp.close()

        try:
            with get_browser_lock(self.port):
                if not self.ensure_chrome():
                    return {"error": "无法启动京东浏览器", "sku": sku}
                data = asyncio.run(_do())
        except Exception as e:
            return {"error": f"京东商品详情异常: {e}", "sku": sku}

        page_state = self._detect_page_state(
            str(data.get("url", "")),
            str(data.get("pageText", "")),
            1 if data.get("name") else 0,
        )
        if page_state in {"login_wall", "captcha"}:
            return {
                "error": self._state_error_message(page_state),
                "error_code": self._state_error_code(page_state),
                "sku": sku,
            }
        if not data.get("name"):
            return {
                "error": "无法从京东商品页解析商品信息",
                "error_code": "JD_PRODUCT_PARSE_FAILED",
                "sku": sku,
            }

        return {
            "sku": sku,
            "name": data.get("name", ""),
            "price": data.get("price", ""),
            "shop": data.get("shop", ""),
            "specs": data.get("specs", ""),
            "url": product_url,
        }

    def close_chrome(self):
        """Cleanly terminate ONLY the Chrome process we launched for JD.

        Uses cdp.close_browser() which terminates only our managed
        process — never touches the user's personal Chrome or other
        browser instances.
        """
        close_browser(self.port)

    def __del__(self):
        """Cleanup on garbage collection — terminate our Chrome if still alive."""
        try:
            close_browser(self.port)
        except Exception:
            pass
