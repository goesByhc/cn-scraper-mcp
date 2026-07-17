"""Taobao/Tmall search engine using curl_cffi + MTOP API signing.

This is the crown jewel — pure Python, NO browser required, not rate-limited.
Uses curl_cffi to impersonate Chrome TLS fingerprint + MTOP HMAC-MD5 signing
to bypass Taobao's anti-bot defenses.

Requirements:
    - curl_cffi installed
    - A valid Taobao cookie file (JSON, exported from a logged-in browser)
      → Path: $TAOBAO_COOKIES_FILE or ~/.cn-scraper-cookies/taobao.json
      → Required cookies: _m_h5_tk, _m_h5_tk_enc, _tb_token_, cookie2, ...
"""

import hashlib
import html as html_lib
import json
import re
import time

from cn_scraper_mcp.auth import CookieFileManager
from cn_scraper_mcp.http import HttpClient

APPKEY = "12574478"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"

# ── custom exceptions ──────────────────────────────────────

class TaobaoAuthError(Exception):
    """Cookie expired or invalid — needs re-login."""
    pass

class TaobaoAPIError(Exception):
    """MTOP API returned an error."""
    pass


# ── engine ──────────────────────────────────────────────────

class TaobaoEngine:
    """Search Taobao/Tmall via MTOP appsearch API.

    Usage:
        engine = TaobaoEngine(cookies_path="taobao_cookies.json")
        result = engine.search("华为mate70", limit=10)
        # result = {"keyword": ..., "total": ..., "items": [...]}
    """

    def __init__(self, cookies_path: str | None = None):
        """Initialize the engine with a cookie file.

        Args:
            cookies_path: Path to JSON cookie file. Falls back to
                          $TAOBAO_COOKIES_FILE, then ~/.cn-scraper-cookies/taobao.json.
        """
        from curl_cffi import requests as creq

        mgr = CookieFileManager("taobao", cookies_path=cookies_path)
        self.cookies = mgr.load()

        self.cookies_path = mgr.resolve_path()
        if not self.cookies:
            raise FileNotFoundError(
                f"Cookie file not found: {mgr.resolve_path()}\n"
                "Export your Taobao cookies from a logged-in browser as JSON.\n"
                "Required keys: _m_h5_tk, _m_h5_tk_enc, _tb_token_, cookie2, "
                "cna, unb, _nk_, ...\n"
                "See README for instructions."
            )
        self.session = creq.Session(impersonate="chrome")
        for k, v in self.cookies.items():
            self.session.cookies.set(k, v, domain=".taobao.com")

        # Shared HTTP client with retry/backoff/rate-limit
        self.http = HttpClient(
            timeout=15,
            max_retries=3,
            backoff_base=1.0,
            rate_limit_interval=0.5,
        )

    # ── internal helpers ─────────────────────────────────

    def _get_token(self) -> str:
        c = self.session.cookies.get("_m_h5_tk")
        return c.split("_")[0] if c else ""

    def _mtop(self, api: str, ver: str, data_dict: dict, tries: int = 4) -> dict:
        """Call the MTOP API with HMAC-MD5 signing.

        Uses HttpClient for transport-level retry/backoff/rate-limiting.
        Retries on TOKEN errors (MTOP-specific, not transport).
        """
        data = json.dumps(data_dict, separators=(",", ":"), ensure_ascii=False)
        last = None

        for attempt in range(tries):
            token = self._get_token()
            t = str(int(time.time() * 1000))
            sign = hashlib.md5(f"{token}&{t}&{APPKEY}&{data}".encode()).hexdigest()

            params = {
                "jsv": "2.7.2",
                "appKey": APPKEY,
                "t": t,
                "sign": sign,
                "api": api,
                "v": ver,
                "type": "originaljson",
                "dataType": "json",
                "H5Request": "true",
                "data": data,
            }

            url = f"https://h5api.m.taobao.com/h5/{api.lower()}/{ver}/"

            # Use HttpClient for transport reliability (timeout, retry, backoff),
            # passing curl_cffi session for TLS fingerprint impersonation
            status, j = self.http.get_json(
                url,
                params=params,
                headers={
                    "Referer": "https://h5.m.taobao.com/",
                    "Accept": "application/json",
                    "Origin": "https://h5.m.taobao.com",
                },
                session=self.session,
            )

            # Transport error
            if status == 0:
                error_msg = j.get("error", "Transport error")
                if attempt < tries - 1:
                    continue
                return {"error": error_msg}

            last = j
            ret = j.get("ret", [])

            # Token refresh via Set-Cookie (MTOP-specific)
            if any("TOKEN" in str(x) for x in ret):
                if attempt < tries - 1:
                    continue  # retry with refreshed token

            return j

        return last or {"error": "All retries exhausted"}

    # ── public API ────────────────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Taobao/Tmall for products.

        Args:
            keyword: Search query (e.g. "华为mate70", "儿童学习桌")
            limit: Max items to return (default 10)

        Returns:
            {"keyword": str, "total": int, "items": [...]}
            Each item: {title, price, origPrice, sales, id, shop, url}
        """
        j = self._mtop(
            "mtop.taobao.wsearch.appsearch",
            "1.0",
            {
                "q": keyword,
                "search_action": "initiative",
                "page": "1",
                "n": "24",
                "sversion": "9.9.9",
            },
        )

        ret = j.get("ret", [])
        ret_str = "::".join(str(r) for r in ret) if isinstance(ret, list) else str(ret)

        if "FAIL_SYS_TOKEN" in ret_str or "SESSION_EXPIRED" in ret_str:
            raise TaobaoAuthError(
                f"Session expired. Refresh your cookies.\nAPI ret: {ret_str}"
            )

        if "SUCCESS" not in ret_str:
            raise TaobaoAPIError(f"MTOP API error: {ret_str}")

        data = j.get("data", {})
        arr = data.get("itemsArray", []) or []  # ⚠️ NOT data["result"] — that's always []

        items = []
        for it in arr[:limit]:
            psi = it.get("priceShowWithIcon") or {}
            price = str(psi.get("price") or it.get("price") or "")
            orig = str(psi.get("originPrice") or "")
            si = it.get("shopInfo") or {}
            shop = (si.get("title") or si.get("nick") or "") if isinstance(si, dict) else ""
            item_id = str(it.get("item_id", ""))

            items.append({
                "title": it.get("title", ""),
                "price": price,
                "origPrice": orig,
                "sales": str(it.get("realSales", "")),
                "id": item_id,
                "shop": shop,
                "url": f"https://item.taobao.com/item.htm?id={item_id}",
            })

        return {
            "keyword": keyword,
            "total": int(data.get("totalResults", 0)),
            "items": items,
        }

    def item_detail(self, item_id: str) -> dict:
        """Get basic detail for a single item.

        Tries MTOP getdetail first, falls back to searching by item_id
        if the detail API returns empty data.
        """
        # Primary: MTOP detail API
        j = self._mtop(
            "mtop.taobao.detail.getdetail",
            "6.0",
            {"id": item_id, "exParams": json.dumps({"id": item_id})},
        )
        d = j.get("data") or {}
        item = d.get("item", {}) or {}
        title = item.get("title", "")
        price_data = item.get("price")
        price = price_data.get("priceMoney") if isinstance(price_data, dict) else price_data
        seller = item.get("seller") or {}
        shop = seller.get("shopName", "") if isinstance(seller, dict) else ""

        if title:
            return self._build_detail(item_id, title, price, shop)

        # The detail MTOP endpoint is frequently rate-limited even while the
        # logged-in item page remains available. Parse the server-rendered page
        # before falling back to search-by-ID.
        page_detail = self._detail_from_page(item_id)
        if page_detail:
            return page_detail

        # Fallback: search for this item_id and check for exact match
        result = self.search(item_id, limit=1)
        items = result.get("items", [])
        if items and str(items[0].get("id", "")) == str(item_id):
            it = items[0]
            return self._build_detail(
                item_id,
                it.get("title", ""),
                float(it.get("price", "0").replace(",", "")) if it.get("price") else None,
                it.get("shop", ""),
            )

        ret = j.get("ret", [])
        ret_str = "::".join(str(value) for value in ret) if isinstance(ret, list) else str(ret)
        if "FAIL_SYS_TOKEN" in ret_str or "SESSION_EXPIRED" in ret_str:
            raise TaobaoAuthError(f"Session expired. API ret: {ret_str}")
        if ret_str and "SUCCESS" not in ret_str:
            raise TaobaoAPIError(f"MTOP detail API error: {ret_str}")
        return {"id": item_id, "error": "商品详情不可用", "url": self._item_url(item_id)}

    def _detail_from_page(self, item_id: str) -> dict | None:
        """Parse stable public fields from the server-rendered item page."""
        url = self._item_url(item_id)
        try:
            # Let curl_cffi's Chrome impersonation supply its native browser
            # headers. HttpClient's mobile fallback UA produces a different
            # Taobao page shape that does not contain the desktop detail data.
            response = self.session.get(url, timeout=self.http.timeout)
        except Exception:
            return None
        if response.status_code != 200 or not response.text:
            return None
        body = response.text

        page_id = re.search(r"var\s+itemId\s*=\s*['\"](\d+)['\"]", body)
        if page_id and page_id.group(1) != str(item_id):
            return None

        title_match = re.search(
            r'<span[^>]*class="[^"]*mainTitle--[^"]*"[^>]*title="([^"]+)"',
            body,
            re.IGNORECASE,
        )
        price_match = re.search(
            r'"sku2info"\s*:\s*\{\s*"0"\s*:\s*\{.*?"priceText"\s*:\s*"([^"]+)"',
            body,
            re.DOTALL,
        ) or re.search(
            r'<span[^>]*class="[^"]*text--[^"]*"[^>]*>([^<]+)</span>',
            body,
            re.IGNORECASE,
        )
        shop_match = re.search(
            r'<span[^>]*class="[^"]*shopName--[^"]*"[^>]*title="([^"]+)"',
            body,
            re.IGNORECASE,
        )

        title = html_lib.unescape(title_match.group(1)).strip() if title_match else ""
        if not title:
            return None
        price = html_lib.unescape(price_match.group(1)).strip() if price_match else None
        shop = html_lib.unescape(shop_match.group(1)).strip() if shop_match else ""
        return self._build_detail(item_id, title, price, shop)

    @staticmethod
    def _item_url(item_id: str) -> str:
        return f"https://item.taobao.com/item.htm?id={item_id}"

    @staticmethod
    def _build_detail(item_id: str, title: str, price: float | str | None, shop: str) -> dict:
        return {
            "id": item_id,
            "title": title,
            "price": price,
            "shop": shop,
            "url": TaobaoEngine._item_url(item_id),
        }


# ── quick test ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    try:
        engine = TaobaoEngine()
        kw = sys.argv[1] if len(sys.argv) > 1 else "华为mate70"
        result = engine.search(kw, limit=5)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
