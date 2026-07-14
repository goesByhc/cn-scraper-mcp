"""Douyin (抖音) engine — search via CDP + hot list via REST API.

Search requires Chrome CDP with logged-in session (captcha must be solved first).
Hot list works via REST API with login cookies.
"""

import asyncio
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from cn_scraper_mcp.http import HttpClient
from cn_scraper_mcp.logging import get_logger

from .cdp import get_browser_lock, is_chrome_running, launch_chrome

logger = get_logger(__name__)


class DouyinEngine:
    """Douyin (抖音) — CDP-based search + REST hot list.

    Usage:
        engine = DouyinEngine(cookies_path="~/.cn-scraper-cookies/douyin.json")
        engine.ensure_chrome()        # launch Chrome, user solves captcha
        results = engine.search("华为", limit=5)
        hot = engine.hot_list()
    """

    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")

    SEARCH_URL = "https://www.douyin.com/search/{}"
    HOT_LIST_URL = "https://www.douyin.com/aweme/v1/web/hot/search/list/"

    def __init__(self, cookies_path: str | None = None, port: int = 9222):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "DOUYIN_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "douyin.json")
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            self.cookies = json.load(open(cookies_path, encoding="utf-8"))
        self.port = port

        self.http = HttpClient(
            default_headers={
                "User-Agent": self.UA,
                "Referer": "https://www.douyin.com/",
            },
            max_retries=2,
        )

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # ── Chrome lifecycle ─────────────────────────────────

    def ensure_chrome(self) -> bool:
        """Ensure Chrome is running with douyin.com loaded.

        Returns:
            True if Chrome is ready, False if launch failed.
        """
        if is_chrome_running(self.port):
            return True

        profile = str(Path.home() / ".cn_scraper_login_douyin")
        proc = launch_chrome(
            self.port, profile,
            url="https://www.douyin.com/",
            headless=False,
        )
        return proc is not None

    # ── search (CDP-based) ───────────────────────────────

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Douyin via CDP. Requires logged-in Chrome with captcha solved.

        Args:
            keyword: Search query
            limit: Max results

        Returns:
            {keyword, count, items: [{title, author, views, duration, date}]}
        """
        enc = urllib.parse.quote(keyword)
        search_url = self.SEARCH_URL.format(enc)

        async def _do():
            import urllib.request as _ur

            import websockets as _ws

            # Find page target
            tg = json.loads(_ur.urlopen(
                f"http://127.0.0.1:{self.port}/json", timeout=5
            ).read())
            page = next((t for t in tg if t["type"] == "page"), None)
            if not page:
                return {"error": "no page target"}

            cdp_timeout = 15

            async with _ws.connect(
                page["webSocketDebuggerUrl"],
                max_size=120_000_000,
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                # Global deadline for all CDP commands
                deadline = asyncio.get_event_loop().time() + 120
                msg_id = 0

                async def cdp_send(method: str, params: dict | None = None) -> dict:
                    nonlocal msg_id, deadline
                    msg_id += 1
                    mid = msg_id
                    await ws.send(json.dumps({
                        "id": mid, "method": method,
                        "params": params or {},
                    }))
                    # Per-message timeout bounded by the global deadline
                    while True:
                        remaining = deadline - asyncio.get_event_loop().time()
                        if remaining <= 0:
                            raise TimeoutError("global CDP deadline exceeded")
                        raw = await asyncio.wait_for(
                            ws.recv(),
                            timeout=min(cdp_timeout, max(1, remaining)),
                        )
                        r = json.loads(raw)
                        if r.get("id") == mid:
                            if "error" in r:
                                raise RuntimeError(f"CDP error: {r['error']}")
                            return r.get("result", {})

                async def cdp_eval(expression: str) -> Any:
                    result = await cdp_send("Runtime.evaluate", {
                        "expression": expression,
                        "returnByValue": True,
                    })
                    return result.get("result", {}).get("value")

                await cdp_send("Page.enable")
                await cdp_send("Page.navigate", {"url": search_url})

                captcha_seen = False

                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(2)

                    try:
                        cap = await cdp_eval(
                            'document.querySelector("iframe[src*=\\"captcha\\"], iframe[src*=\\"verify\\"]") !== null'
                        )
                    except (TimeoutError, RuntimeError):
                        continue

                    if cap:
                        if not captcha_seen:
                            captcha_seen = True
                            logger.warning("douyin_search: captcha detected, waiting for user to solve...")
                        continue
                    elif captcha_seen:
                        logger.info("douyin_search: captcha solved, checking for results...")
                        captcha_seen = False

                    try:
                        check = await cdp_eval(
                            '(function(){var c=document.querySelector("#search-result-container");'
                            'return c&&c.innerText.length>100?"loaded":"waiting"})()'
                        )
                    except (TimeoutError, RuntimeError):
                        continue

                    if check != "loaded":
                        continue

                    try:
                        raw = await cdp_eval(
                            '''(function(){
                                var items=document.querySelectorAll("#search-result-container div[class]");
                                var results=[],seen=new Set();
                                items.forEach(function(el){
                                    var t=(el.innerText||"").trim();
                                    if(t.length<60||seen.has(t.substring(0,40)))return;
                                    seen.add(t.substring(0,40));
                                    var lines=t.split("\\n").filter(function(l){return l.trim()});
                                    var title=lines[2]||lines[1]||"";
                                    var author=((lines[3]||"").match(/@\\S+/)||[""])[0];
                                    var views=((lines[1]||"").match(/[\\d.]+万/)||[""])[0];
                                    var duration=((lines[0]||"").match(/\\d{2}:\\d{2}/)||[""])[0];
                                    results.push({title:title,author:author,views:views,duration:duration,date:lines[4]||""});
                                });
                                return JSON.stringify(results);
                            })()'''
                        )
                        return json.loads(raw) if isinstance(raw, str) else (raw or [])
                    except (TimeoutError, RuntimeError):
                        continue

                try:
                    cap_final = await cdp_eval(
                        'document.querySelector("iframe[src*=\\"captcha\\"]") !== null'
                    )
                except (TimeoutError, RuntimeError):
                    cap_final = False

                if cap_final:
                    return {
                        "error": "captcha",
                        "hint": (
                            "\u23f3 请在 Chrome 窗口中完成人机验证。\n"
                            "Chrome 已打开抖音搜索页，有一个验证码需要手动过一下。\n"
                            "过完后重新调用 douyin_search 即可。"
                        ),
                    }
                return {
                    "error": "timeout",
                    "hint": "搜索超时 (120s)，请确认抖音页面已加载完成",
                }

        # ── Acquire port lock BEFORE ensure_chrome to prevent races ──
        acquire_timeout = 120
        lock = get_browser_lock(self.port)
        acquired = lock.acquire(timeout=acquire_timeout)
        if not acquired:
            return {"keyword": keyword, "error": "lock_timeout",
                    "hint": f"端口 {self.port} 当前被其他操作占用，请稍后重试"}
        try:
            if not self.ensure_chrome():
                return {"error": "无法启动 Chrome", "keyword": keyword,
                        "hint": "请确认 Chrome 已安装，或设置 CHROME_PATH 环境变量。"}
            items = asyncio.run(_do())
        except Exception as e:
            return {"keyword": keyword, "error": f"搜索异常: {e}"}
        finally:
            lock.release()

        if isinstance(items, dict):
            items["keyword"] = keyword
            return items

        return {
            "keyword": keyword,
            "count": len(items[:limit]),
            "items": items[:limit],
        }

    # ── hot list (REST API) ──────────────────────────────

    def hot_list(self) -> dict:
        """Get Douyin trending search list. Requires login cookies.

        Returns:
            {count, items: [{word, hot_value, position, label}]}
        """
        if not self.cookies:
            return {"error": "抖音热搜需要登录", "hint": "请用 guided_login 登录后收割 cookie"}

        headers = {"Cookie": self._cookie_str()}
        status, data = self.http.get_json(self.HOT_LIST_URL, headers=headers)

        if status == 0:
            return {"error": data.get("error", "请求失败")}
        if status >= 400:
            return {"error": f"HTTP {status}"}

        word_list = data.get("data", {}).get("word_list", [])
        items = []
        for w in word_list:
            info = w.get("word_record", w.get("sentence_info", w))
            items.append({
                "word": info.get("word", "") or w.get("word", ""),
                "hot_value": info.get("hot_value", 0),
                "position": w.get("position", 0),
                "label": f"热{w.get('position', 0)}" if w.get("position") else "",
            })

        return {"count": len(items), "items": items}
