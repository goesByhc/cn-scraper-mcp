"""Pinduoduo (拼多多) search engine via Chrome CDP.

Pinduoduo is the HARDEST of all Chinese e-commerce platforms to scrape.
Key limitations documented honestly below.

── PLATFORM REALITY ────────────────────────────────────────────

CRITICAL: PDD mobile search allows EXACTLY ONE query per browser
session. After the first search, all subsequent searches return
"系统繁忙" (System Busy) — this is server-side rate limiting, not
something we can bypass with retries or delays.

Anti-bot defenses:
- anti_content token: computed by page JavaScript, NOT reproducible
  via curl/pure Python. Requires a real browser engine.
- iPhone UA required: desktop UAs are immediately blocked.
- PDDAccessToken + pdd_user_id cookies: short-lived (~1 hour),
  must be harvested from a logged-in mobile browser session.

Auth cookie lifetime:
- Tokens expire in ~1 hour. After expiry, pages redirect to login
  (detectable via og:title='拼多多商城' with no product data).

── USAGE ────────────────────────────────────────────────────────

    engine = PDDEngine(cookies_path="~/.cn-scraper-cookies/pdd.json")
    result = engine.search("儿童学习桌")  # ONE search only
    # Second search raises PDDRateLimitError!

    detail = engine.product_detail("123456789")
    # Product detail works independently of search limit

── COOKIE FILE FORMAT ───────────────────────────────────────────

    {
        "PDDAccessToken": "xxx...",
        "pdd_user_id": "1234567890",
        "pdd_user_name": "user_name_xxx"
    }

Requirements:
    - Chrome installed (headful required)
    - Cookie file at ~/.cn-scraper-cookies/pdd.json
    - Each engine instance = ONE search maximum
"""

import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .cdp import CDPClient, close_browser, is_chrome_running, launch_chrome

# Default debug port for PDD
PDD_PORT = 9255

# iPhone UA required by PDD mobile search
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/15.0 Mobile/15E148 Safari/604.1"
)


# ── Custom PDD-specific exceptions ──────────────────────────────


class PDDRateLimitError(Exception):
    """PDD rate-limited the session — '系统繁忙' detected.

    This is the single-search limitation: PDD allows ONE query per
    browser session. After that, every query returns '系统繁忙'.
    You MUST create a new PDDEngine instance to search again.
    """
    pass


class PDDAuthError(Exception):
    """PDD cookie expired or invalid — login required.

    PDDAccessToken + pdd_user_id are short-lived (~1 hour).
    Re-harvest cookies from a logged-in mobile browser session.
    """
    pass


class PDDParseError(Exception):
    """Failed to parse PDD search results — page structure changed."""
    pass


class PDDSoldOutError(Exception):
    """Product is sold out (status=5, status_explain='商品已售罄')."""
    pass


# ── JS extractors (run in browser via CDP) ──────────────────────

SEARCH_EXTRACT_JS = r"""
(function(){
  // PDD mobile search page data extraction
  var bodyText = (document.body && document.body.innerText || '').substring(0, 5000);
  var url = window.location.href;
  var title = document.title || '';

  // ── Rate-limit detection ─────────────────────────
  var rateLimited = false;
  if (bodyText.indexOf('系统繁忙') >= 0 || bodyText.indexOf('网络异常') >= 0) {
    rateLimited = true;
  }

  // ── Login redirect detection ─────────────────────
  var ogTitle = '';
  var ogMeta = document.querySelector('meta[property="og:title"]');
  if (ogMeta) ogTitle = ogMeta.getAttribute('content') || '';

  // ── Extract product items ────────────────────────
  var items = [];

  // PDD mobile search results are often in divs with data-goods-id or class containing 'goods'
  var goodsCards = document.querySelectorAll('[data-goods-id], .goods-item, [class*="goods-item"], [class*="search-result"] div[class*="goods"]');
  if (goodsCards.length === 0) {
    // Fallback: scan all divs looking for ¥ price patterns
    goodsCards = document.querySelectorAll('div');
  }

  goodsCards.forEach(function(el){
    var text = el.innerText || '';
    // Quick check: has a ¥ price
    if (text.indexOf('¥') < 0 && text.indexOf('￥') < 0) return;

    var goodsId = el.getAttribute('data-goods-id') || '';

    // Try to find name — first line before price
    var lines = text.split('\n').map(function(l){ return l.trim(); }).filter(function(l){ return l.length > 0; });
    var name = '';
    var price = null;
    var sold = 0;

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      var priceMatch = line.match(/[¥￥]\s*([\d,.]+)/);
      if (priceMatch && !price) {
        price = parseFloat(priceMatch[1].replace(/,/g, ''));
        // Name is the line before the price
        if (i > 0 && lines[i-1].indexOf('¥') < 0 && lines[i-1].indexOf('￥') < 0) {
          name = lines[i-1];
        }
      }
      // Sold count
      var soldMatch = line.match(/(\d+[\.\d]*万?\+?)\s*件/);
      if (soldMatch && !sold) {
        var s = soldMatch[1];
        if (s.indexOf('万') >= 0) {
          sold = Math.round(parseFloat(s) * 10000);
        } else {
          sold = parseInt(s.replace('+', ''), 10);
        }
      }
    }

    // Fallback name from first meaningful line
    if (!name && lines.length > 0) {
      for (var j = 0; j < Math.min(lines.length, 5); j++) {
        var l = lines[j];
        if (l.indexOf('¥') < 0 && l.indexOf('￥') < 0 && l.length > 3) {
          name = l;
          break;
        }
      }
    }

    if (name || goodsId) {
      items.push({
        goodsId: goodsId,
        name: name,
        price: price,
        sold: sold
      });
    }
  });

  return JSON.stringify({
    url: url,
    title: title,
    ogTitle: ogTitle,
    pageText: bodyText,
    rateLimited: rateLimited,
    itemCount: items.length,
    items: items
  });
})()
"""

DETAIL_EXTRACT_JS = r"""
(function(){
  var bodyText = (document.body && document.body.innerText || '').substring(0, 10000);
  var url = window.location.href;
  var title = document.title || '';

  // ── Sold-out detection ───────────────────────────
  var soldOut = false;
  if (bodyText.indexOf('商品已售罄') >= 0 || bodyText.indexOf('已卖光') >= 0) {
    soldOut = true;
  }

  // ── Login-gated ──────────────────────────────────
  var ogTitle = '';
  var ogMeta = document.querySelector('meta[property="og:title"]');
  if (ogMeta) ogTitle = ogMeta.getAttribute('content') || '';
  var loginGated = (ogTitle === '拼多多商城' && bodyText.indexOf('¥') < 0 && bodyText.indexOf('￥') < 0);

  // ── Extract: name, price, spec info from body text ─
  var lines = bodyText.split('\n').map(function(l){ return l.trim(); }).filter(function(l){ return l.length > 0; });
  var name = '';
  var price = null;
  var origPrice = null;
  var sales = '';
  var specs = [];

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i];
    // Price detection
    var priceMatch = line.match(/[¥￥]\s*([\d,.]+)/);
    if (priceMatch) {
      var p = parseFloat(priceMatch[1].replace(/,/g, ''));
      if (price === null) {
        price = p;
      } else if (origPrice === null && p < price) {
        origPrice = price;
        price = p;
      } else if (origPrice === null) {
        origPrice = p;
      }
    }
    // Name: first non-price, non-blank line that looks like a product name
    if (!name && line.indexOf('¥') < 0 && line.indexOf('￥') < 0 && line.length > 5) {
      name = line;
    }
    // Sales
    var soldMatch = line.match(/(\d+[\.\d]*万?\+?)\s*件/);
    if (soldMatch) {
      sales = soldMatch[1];
    }
    // Specs (lines containing 颜色/尺码/规格)
    if (line.indexOf('颜色') >= 0 || line.indexOf('尺码') >= 0 || line.indexOf('规格') >= 0) {
      specs.push(line);
    }
  }

  return JSON.stringify({
    url: url,
    title: title,
    ogTitle: ogTitle,
    loginGated: loginGated,
    soldOut: soldOut,
    name: name,
    price: price,
    origPrice: origPrice,
    sales: sales,
    specs: specs,
    pageText: bodyText.substring(0, 3000)
  });
})()
"""


# ── Engine ───────────────────────────────────────────────────────


class PDDEngine:
    """Search Pinduoduo via headful Chrome CDP with iPhone UA.

    ⚠️ CRITICAL LIMITATION: PDD allows ONE search per browser session.
    After the first search, "系统繁忙" is returned for all subsequent
    attempts. This is server-side rate limiting — not a bug.

    To search again, create a NEW PDDEngine instance which will launch
    a fresh browser session.

    Usage:
        engine = PDDEngine(cookies_path="~/.cn-scraper-cookies/pdd.json")
        engine.ensure_chrome()
        result = engine.search("儿童学习桌")
        # result = {"keyword": ..., "count": ..., "items": [...]}

        # Second search WILL FAIL — create new instance:
        engine2 = PDDEngine()
        engine2.ensure_chrome()
        result2 = engine2.search("手机壳")

        # Product detail works independently:
        detail = engine.product_detail("123456789")
    """

    def __init__(self, cookies_path: str | None = None, port: int = PDD_PORT):
        """Initialize the PDD engine.

        Args:
            cookies_path: Path to JSON cookie file containing
                          PDDAccessToken, pdd_user_id, pdd_user_name.
                          Falls back to ~/.cn-scraper-cookies/pdd.json.
            port: CDP debug port (default 9255).
        """
        if cookies_path is None:
            cookies_path = str(
                Path.home() / ".cn-scraper-cookies" / "pdd.json"
            )

        self.cookies_path = cookies_path
        self.port = port
        self._cdp: CDPClient | None = None
        self._searched: bool = False  # Track single-search limit
        self._cookies: dict[str, str] = {}

        # Load cookies if available
        if os.path.exists(cookies_path):
            try:
                with open(cookies_path, encoding="utf-8") as f:
                    self._cookies = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    @property
    def has_valid_cookies(self) -> bool:
        """Check if cookie file has the required PDD tokens."""
        return bool(
            self._cookies.get("PDDAccessToken")
            and self._cookies.get("pdd_user_id")
        )

    # ── Chrome lifecycle ─────────────────────────────────────

    def ensure_chrome(self) -> bool:
        """Ensure Chrome is running on the PDD debug port.

        If Chrome is already running → do nothing.
        If not → launch it (headful, with a fresh profile).

        Returns True if Chrome is now running.
        """
        if is_chrome_running(self.port):
            return True

        # PDD needs headful (anti-bot detects headless)
        return launch_chrome(
            self.port,
            str(Path.home() / ".pdd_chrome_profile"),
            url="about:blank",
            headless=False,
        )

    # ── Cookie injection (sets iPhone UA + cookies) ──────────

    async def _inject_cookies(self, cdp: CDPClient) -> None:
        """Inject PDD cookies and iPhone UA into the browser session.

        This must be called BEFORE navigating to any PDD page.
        The cookies are set on .yangkeduo.com domain.
        """
        # Set user agent override for mobile emulation
        await cdp._send("Network.setUserAgentOverride", {
            "userAgent": IPHONE_UA,
            "platform": "iPhone",
            "acceptLanguage": "zh-CN,zh;q=0.9",
        })

        # Set viewport to iPhone size
        await cdp._send("Emulation.setDeviceMetricsOverride", {
            "width": 375,
            "height": 812,
            "deviceScaleFactor": 3,
            "mobile": True,
        })

        # Inject cookies
        if self._cookies:
            cookie_params = []
            for name, value in self._cookies.items():
                cookie_params.append({
                    "name": name,
                    "value": str(value),
                    "domain": ".yangkeduo.com",
                    "path": "/",
                    "httpOnly": False,
                    "secure": True,
                    "sameSite": "Lax",
                })

            if cookie_params:
                await cdp._send("Network.setCookies", {"cookies": cookie_params})

    # ── Core search logic ────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Pinduoduo for products.

        ⚠️ ONE SEARCH PER INSTANCE. After calling this method,
        the instance is exhausted. Create a new PDDEngine to search
        again.

        Args:
            keyword: Search query (e.g. "儿童学习桌")
            limit: Max items to return (default 10)

        Returns:
            {"keyword": str, "count": int, "items": [...]}
            Each item: {goodsId, name, price, sold, url}

        Raises:
            PDDRateLimitError: PDD returned "系统繁忙" (single-search limit)
            PDDAuthError: Cookie expired, redirected to login
            PDDParseError: Page structure changed, can't parse
        """
        import asyncio

        if self._searched:
            raise PDDRateLimitError(
                "PDD allows only ONE search per browser session. "
                "This engine instance has already been used. "
                "Create a new PDDEngine() to search again.\n"
                "See docs: PDD mobile search rate-limits after the first query."
            )

        if not self.ensure_chrome():
            return {
                "error": "无法启动拼多多浏览器",
                "hint": (
                    "请确保 Chrome 已安装。"
                    f"Cookie 路径: {self.cookies_path}\n"
                    "需要 PDDAccessToken 和 pdd_user_id cookie。"
                ),
            }

        if not self.has_valid_cookies:
            return {
                "error": "拼多多 cookie 无效或缺失",
                "hint": (
                    f"Cookie 文件 ({self.cookies_path}) 需要包含 "
                    "PDDAccessToken 和 pdd_user_id。\n"
                    "从已登录的拼多多手机浏览器中导出。\n"
                    "⚠️ Token 有效期约 1 小时，需定期刷新。"
                ),
            }

        enc = urllib.parse.quote(keyword)
        search_url = (
            f"https://mobile.yangkeduo.com/search_result.html"
            f"?search_key={enc}&search_type=goods"
        )

        async def _do_search():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect()
                await cdp.enable()
                await self._inject_cookies(cdp)
                await cdp.navigate(search_url, wait=6)
                await asyncio.sleep(2)  # Extra wait for JS rendering

                raw = await cdp.evaluate(SEARCH_EXTRACT_JS, return_by_value=True)
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw or {
                    "url": "", "title": "", "ogTitle": "",
                    "pageText": "", "rateLimited": False,
                    "itemCount": 0, "items": [],
                }
            finally:
                await cdp.close()

        try:
            raw_result = asyncio.run(_do_search())
        except Exception as e:
            self._searched = True
            return {"error": f"拼多多搜索异常: {e}"}

        self._searched = True  # Mark as searched regardless of outcome

        # ── Page state detection ───────────────────────────
        return self._process_search_result(raw_result, keyword, limit)

    def _process_search_result(
        self, raw: dict, keyword: str, limit: int
    ) -> dict:
        """Process raw CDP extraction data into structured results.

        Args:
            raw: Dict from SEARCH_EXTRACT_JS evaluation
            keyword: Original search keyword
            limit: Max items to return

        Returns:
            Structured search result dict
        """
        # ── Rate-limit detection ──────────────────────────
        if raw.get("rateLimited"):
            raise PDDRateLimitError(
                "拼多多返回 '系统繁忙' — 已达到单次搜索限制。\n"
                "PDD mobile search 每个浏览器会话只允许一次搜索。\n"
                "请创建新的 PDDEngine() 实例重新搜索。"
            )

        page_text = raw.get("pageText", "")
        og_title = raw.get("ogTitle", "")
        item_count = raw.get("itemCount", 0)
        items_raw = raw.get("items", [])

        # ── Login-gated detection ─────────────────────────
        if og_title == "拼多多商城" and item_count == 0:
            raise PDDAuthError(
                "拼多多 cookie 已过期（页面重定向至登录）。\n"
                f"Cookie 文件: {self.cookies_path}\n"
                "PDDAccessToken 有效期约 1 小时，请重新从手机浏览器导出。"
            )

        # ── Empty results (genuine) ───────────────────────
        if item_count == 0 and len(items_raw) == 0:
            # Check for other error signals in page text
            if "系统繁忙" in page_text or "网络异常" in page_text:
                raise PDDRateLimitError(
                    "拼多多返回 '系统繁忙' — 已达到单次搜索限制。\n"
                    "请创建新的 PDDEngine() 实例重新搜索。"
                )
            return {
                "keyword": keyword,
                "count": 0,
                "items": [],
                "state": "empty",
            }

        # ── Deduplicate and format items ──────────────────
        seen_ids: set = set()
        items_out: list[dict[str, Any]] = []

        for it in items_raw:
            goods_id = (it.get("goodsId") or "").strip()
            # Generate synthetic ID from name if no goodsId
            if not goods_id and it.get("name"):
                goods_id = f"name:{hash(it['name']) & 0xFFFFFFFF}"

            if not goods_id or goods_id in seen_ids:
                continue
            seen_ids.add(goods_id)

            price = it.get("price")
            # Convert price to float if needed
            if price is not None and not isinstance(price, (int, float)):
                try:
                    price = float(price)
                except (TypeError, ValueError):
                    price = None

            url = ""
            if goods_id and not goods_id.startswith("name:"):
                url = f"https://mobile.yangkeduo.com/goods2.html?goods_id={goods_id}"

            items_out.append({
                "goodsId": (goods_id if not goods_id.startswith("name:") else ""),
                "name": it.get("name", ""),
                "price": price,
                "sold": it.get("sold", 0),
                "url": url,
            })

        return {
            "keyword": keyword,
            "count": len(items_out),
            "items": items_out[:limit],
            "state": "ok",
        }

    # ── Product detail ─────────────────────────────────────

    def product_detail(self, url_or_id: str) -> dict:
        """Get product detail from PDD.

        Opens the goods2.html page and extracts name, price,
        original price, sales, and specs.

        Product detail works independently of the single-search
        limit — it does NOT count as a search.

        Args:
            url_or_id: Product goods_id (e.g. "123456789") or
                      full goods2.html URL.

        Returns:
            {
                "goodsId": str,
                "name": str | None,
                "price": float | None,
                "origPrice": float | None,
                "sales": str,
                "specs": [str],
                "url": str,
                "soldOut": bool,
            }

        Raises:
            PDDAuthError: Cookie expired, login required
            PDDSoldOutError: Product is sold out
        """
        import asyncio

        # Parse URL or ID
        goods_id = url_or_id
        if "goods_id=" in url_or_id:
            match = re.search(r"goods_id=(\d+)", url_or_id)
            if match:
                goods_id = match.group(1)

        detail_url = f"https://mobile.yangkeduo.com/goods2.html?goods_id={goods_id}"

        if not self.ensure_chrome():
            return {
                "error": "无法启动拼多多浏览器",
                "hint": "请确保 Chrome 已安装。",
            }

        async def _do_detail():
            cdp = CDPClient(self.port)
            try:
                await cdp.connect()
                await cdp.enable()
                await self._inject_cookies(cdp)
                await cdp.navigate(detail_url, wait=5)
                await asyncio.sleep(2)

                raw = await cdp.evaluate(DETAIL_EXTRACT_JS, return_by_value=True)
                if isinstance(raw, str):
                    return json.loads(raw)
                return raw or {}
            finally:
                await cdp.close()

        try:
            raw_result = asyncio.run(_do_detail())
        except Exception as e:
            return {"error": f"拼多多商品详情获取异常: {e}"}

        # ── State detection ────────────────────────────────
        if raw_result.get("loginGated"):
            raise PDDAuthError(
                "拼多多 cookie 已过期（商品页重定向至登录）。\n"
                "PDDAccessToken 有效期约 1 小时，请刷新 cookie。"
            )

        if raw_result.get("soldOut"):
            return {
                "goodsId": goods_id,
                "name": raw_result.get("name"),
                "price": raw_result.get("price"),
                "origPrice": raw_result.get("origPrice"),
                "sales": raw_result.get("sales", ""),
                "specs": raw_result.get("specs", []),
                "url": detail_url,
                "soldOut": True,
                "state": "sold_out",
            }

        if not raw_result.get("name") and not raw_result.get("price"):
            # Check if it's a 404 or similar
            page_text = raw_result.get("pageText", "")
            if "商品已下架" in page_text or "不存在" in page_text:
                return {
                    "goodsId": goods_id,
                    "name": None,
                    "price": None,
                    "origPrice": None,
                    "sales": "",
                    "specs": [],
                    "url": detail_url,
                    "soldOut": False,
                    "state": "not_found",
                }

        return {
            "goodsId": goods_id,
            "name": raw_result.get("name"),
            "price": raw_result.get("price"),
            "origPrice": raw_result.get("origPrice"),
            "sales": raw_result.get("sales", ""),
            "specs": raw_result.get("specs", []),
            "url": detail_url,
            "soldOut": False,
            "state": "ok",
        }

    # ── Cleanup ──────────────────────────────────────────

    def close_chrome(self):
        """Cleanly terminate ONLY the Chrome process we launched for PDD.

        Uses cdp.close_browser() which terminates only our managed
        process — never touches the user's personal Chrome.
        """
        close_browser(self.port)

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            close_browser(self.port)
        except Exception:
            pass


# ── quick test ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        engine = PDDEngine()
        kw = sys.argv[1] if len(sys.argv) > 1 else "儿童学习桌"
        result = engine.search(kw, limit=5)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except PDDRateLimitError as e:
        print(f"RATE LIMIT: {e}")
    except PDDAuthError as e:
        print(f"AUTH ERROR: {e}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
