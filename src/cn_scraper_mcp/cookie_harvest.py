"""CDP-based cookie auto-harvest module.

Uses Chrome DevTools Protocol (CDP) Network.getAllCookies to extract all
cookies — **including HttpOnly** cookies that are invisible to JavaScript.

This tool harvests cookies from the **user's own browser session**.  The
browser must already be running with --remote-debugging-port and the user
must already be logged into the target platform.  It does NOT steal cookies
or interact with sites the user hasn't explicitly logged into.

Usage:
    from cn_scraper_mcp.cookie_harvest import CookieHarvester

    harvester = CookieHarvester()
    result = harvester.harvest("taobao", port=9222)
    # → {platform: "taobao", count: 17, saved_to: "~/.cn-scraper-cookies/taobao.json", status: "ok"}
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import websockets

from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.cookie_harvest")

# ── Platform domain registry ───────────────────────────────────────

PLATFORM_DOMAINS: dict[str, str] = {
    "taobao": ".taobao.com",
    "xiaohongshu": ".xiaohongshu.com",
    "zhihu": ".zhihu.com",
    "zsxq": ".zsxq.com",
    "jd": ".jd.com",
    "pdd": ".yangkeduo.com",
    "weibo": ".weibo.com",
}

DEFAULT_PORTS: dict[str, int] = {
    "jd": 9247,
    "xiaohongshu": 9251,
    "pdd": 9223,
}

DEFAULT_PORT: int = 9222

COOKIE_DIR: Path = Path.home() / ".cn-scraper-cookies"
"""Hardcoded save directory for security — never user-overridable."""

# ── Harvester ──────────────────────────────────────────────────────


class CookieHarvestError(Exception):
    """Cookie harvest failed — CDP connection, no page targets, or I/O error."""

    pass


class CookieHarvester:
    """Extract cookies from the user's own browser via Chrome DevTools Protocol.

    Connects to a running Chrome/Chromium instance (already launched with
    --remote-debugging-port), calls ``Network.getAllCookies``, filters
    cookies for the target platform domain, and saves them as a JSON dict
    to ``~/.cn-scraper-cookies/<platform>.json``.

    This class is designed for one-shot use: instantiate and call
    ``harvest()``.  There is no persistent websocket — each harvest opens a
    fresh connection, extracts cookies, and disconnects.

    SECURITY:
        - Cookie VALUES are NEVER logged — only names and counts.
        - The save directory is hardcoded to ``~/.cn-scraper-cookies/``.
        - This only reads the user's OWN browser that they must have
          already launched and logged into.
    """

    def __init__(self) -> None:
        self._msg_id: int = 0

    # ── public API ───────────────────────────────────────────

    def harvest(self, platform: str, port: int | None = None) -> dict:
        """Extract cookies for *platform* from the browser on *port*.

        Args:
            platform: Platform name — one of:
                      ``taobao``, ``xiaohongshu``, ``zhihu``, ``zsxq``,
                      ``jd``, ``pdd``.
            port:     CDP debug port the browser is listening on.
                      Defaults per platform (jd→9247, xiaohongshu→9251,
                      pdd→9223, others→9222).

        Returns:
            ``{platform, count, saved_to, status}``

        Raises:
            ValueError:       ``platform`` is not in the supported list.
            CookieHarvestError: CDP connection or protocol error.
        """
        if platform not in PLATFORM_DOMAINS:
            raise ValueError(
                f"Unsupported platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(PLATFORM_DOMAINS))}"
            )

        if port is None:
            port = DEFAULT_PORTS.get(platform, DEFAULT_PORT)

        domain = PLATFORM_DOMAINS[platform]

        logger.info(
            "Harvesting cookies for platform=%s domain=%s port=%s",
            platform, domain, port,
        )
        return asyncio.run(self._harvest_async(platform, port, domain))

    # ── async internals ──────────────────────────────────────

    async def _harvest_async(
        self,
        platform: str,
        port: int,
        domain: str,
    ) -> dict:
        # 1. Find a page target to get a WebSocket URL
        try:
            targets = self._get_json(port, "/json")
        except (OSError, json.JSONDecodeError, urllib.error.URLError) as e:
            raise CookieHarvestError(
                f"Cannot reach CDP on port {port}: {e}. "
                "Is Chrome running with --remote-debugging-port?"
            ) from e

        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            raise CookieHarvestError(
                f"No page target found on port {port}. "
                "Open at least one tab in Chrome before harvesting."
            )

        ws_url = pages[0]["webSocketDebuggerUrl"]
        self._msg_id = 0

        # 2. Connect WS, send Network.enable + Network.getAllCookies
        try:
            async with websockets.connect(
                ws_url, max_size=120_000_000, open_timeout=10, close_timeout=5,
            ) as ws:
                await self._cdp_cmd(ws, "Network.enable")
                result = await self._cdp_cmd(ws, "Network.getAllCookies")
        except (OSError, asyncio.TimeoutError) as e:
            raise CookieHarvestError(
                f"WebSocket connection failed on port {port}: {e}"
            ) from e

        all_cookies: list[dict] = result.get("cookies", [])

        # 3. Filter cookies that belong to the platform domain
        platform_cookies: list[dict] = [
            c for c in all_cookies
            if domain in (c.get("domain", "") or "")
        ]

        # 4. Build flat name→value dict (compatible with all engines)
        cookie_dict: dict[str, str] = {}
        for c in platform_cookies:
            name = c.get("name", "")
            if name:
                cookie_dict[name] = c.get("value", "")

        # 5. Guard: never overwrite existing valid cookies with empty harvest
        if not cookie_dict:
            logger.warning(
                "Zero cookies harvested for %s (domain=%s, port=%d). "
                "Existing cookie file preserved.",
                platform, domain, port,
            )
            return {
                "platform": platform,
                "count": 0,
                "saved_to": None,
                "status": "empty",
                "hint": (
                    f"未找到 {platform} 的 Cookie。请确认浏览器已登录 {domain} 。\n"
                    f"端口 {port} 是否正确？"
                ),
            }

        # 6. Atomic save: write to temp file, then replace
        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        save_path = COOKIE_DIR / f"{platform}.json"
        tmp_path = save_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
        tmp_path.replace(save_path)  # atomic on same filesystem

        # Log names ONLY — never values
        cookie_names = sorted(cookie_dict.keys())
        logger.info(
            "Harvested %d cookies for %s: names=%s saved_to=%s",
            len(cookie_names), platform, cookie_names, save_path,
        )

        return {
            "platform": platform,
            "count": len(cookie_dict),
            "saved_to": str(save_path),
            "status": "ok",
        }

    # ── CDP helpers ──────────────────────────────────────────

    def _get_json(self, port: int, path: str) -> Any:
        """GET a JSON endpoint on the CDP HTTP server."""
        url = f"http://127.0.0.1:{port}{path}"
        resp = urllib.request.urlopen(url, timeout=5)
        return json.loads(resp.read())

    async def _cdp_cmd(
        self,
        ws: Any,
        method: str,
        params: dict | None = None,
    ) -> dict:
        """Send a CDP command over the websocket and return its result."""
        self._msg_id += 1
        mid = self._msg_id
        msg = {"id": mid, "method": method, "params": params or {}}
        await ws.send(json.dumps(msg))

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            resp = json.loads(raw)
            if resp.get("id") == mid:
                if "error" in resp:
                    raise CookieHarvestError(
                        f"CDP error for {method}: {resp['error']}"
                    )
                return resp.get("result", {})
