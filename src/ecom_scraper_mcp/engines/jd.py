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

import json, os, sys, urllib.parse
from pathlib import Path
from typing import Optional

from .cdp import CDPClient, find_chrome, is_chrome_running, launch_chrome


# Default debug port for JD
JD_PORT = 9247

# ── JS extractor (extract.js ported inline) ─────────────────

EXTRACT_JS = r"""
(function(){
  var items = [];
  var cards = document.querySelectorAll('div[data-sku]');
  cards.forEach(function(el){
    var sku = el.getAttribute('data-sku') || '';
    var img = el.querySelector('img');
    var name = (img && img.getAttribute('alt')) || '';
    var priceEl = el.querySelector('span');
    var ad = el.innerText.indexOf('广告') >= 0;
    var price = null;
    if (priceEl) {
      var m = priceEl.innerText.match(/[¥￥]\s*([\d,.]+)/);
      if (m) price = parseFloat(m[1].replace(/,/g,''));
    }
    items.push({sku: sku, name: name, price: price, ad: ad});
  });
  return JSON.stringify({count: items.length, items: items});
})()
"""


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

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search JD for products. Launches Chrome if needed.

        Args:
            keyword: Search query
            limit: Max items to return

        Returns:
            {"keyword": str, "count": int, "items": [...]}
            Each item: {sku, name, price, ad}
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
        search_url = f"https://search.jd.com/Search?keyword={enc}&enc=utf-8"

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
                return raw or {"count": 0, "items": []}
            finally:
                await cdp.close()

        try:
            result = asyncio.run(_do_search())
        except Exception as e:
            return {"error": f"京东搜索异常: {e}"}

        items = []
        for it in result.get("items", [])[:limit]:
            items.append({
                "sku": it.get("sku", ""),
                "name": it.get("name", ""),
                "price": it.get("price"),
                "ad": it.get("ad", False),
                "url": f"https://item.jd.com/{it.get('sku', '')}.html" if it.get("sku") else "",
            })

        return {
            "keyword": keyword,
            "count": result.get("count", len(items)),
            "items": items,
        }

    def close_chrome(self):
        """Kill the JD Chrome process."""
        import subprocess
        subprocess.run(
            ["taskkill", "//F", "//IM", "chrome.exe"],
            capture_output=True,
            shell=True,  # cmd builtin on Windows
        )
