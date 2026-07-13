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

import hashlib, json, os, time
from pathlib import Path
from typing import Optional

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

    def __init__(self, cookies_path: Optional[str] = None):
        """Initialize the engine with a cookie file.

        Args:
            cookies_path: Path to JSON cookie file. Falls back to
                          $TAOBAO_COOKIES_FILE, then ~/.cn-scraper-cookies/taobao.json.
        """
        from curl_cffi import requests as creq

        if cookies_path is None:
            cookies_path = os.environ.get(
                "TAOBAO_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "taobao.json")

        if not os.path.exists(cookies_path):
            raise FileNotFoundError(
                f"Cookie file not found: {cookies_path}\n"
                "Export your Taobao cookies from a logged-in browser as JSON.\n"
                "Required keys: _m_h5_tk, _m_h5_tk_enc, _tb_token_, cookie2, "
                "cna, unb, _nk_, ...\n"
                "See README for instructions."
            )

        self.cookies = json.load(open(cookies_path, encoding="utf-8"))
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
        """Get basic detail for a single item (price, title, shop).
        
        Note: full detail needs a different MTOP API. This is a lightweight version.
        """
        j = self._mtop(
            "mtop.taobao.detail.getdetail",
            "6.0",
            {"id": item_id, "exParams": json.dumps({"id": item_id})},
        )
        d = j.get("data", {})
        item = d.get("item", {}) or {}
        return {
            "id": item_id,
            "title": item.get("title", ""),
            "price": item.get("price", {}).get("priceMoney", 0) if item.get("price") else None,
            "shop": item.get("seller", {}).get("shopName", "") if item.get("seller") else "",
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
