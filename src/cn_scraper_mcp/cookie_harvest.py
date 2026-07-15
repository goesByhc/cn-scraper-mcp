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
from typing import Any

import websockets

from cn_scraper_mcp.engines.cdp import close_browser, is_chrome_running, launch_chrome
from cn_scraper_mcp.logging import get_logger
from cn_scraper_mcp.session import (
    COOKIE_DIR,  # re-export for backward compat
    DEFAULT_CDP_PORT,
    get_login_signal_cookies,
    get_profile_dir,
    is_profile_platform,
)

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
    "douyin": ".douyin.com",
}

DEFAULT_PORTS: dict[str, int] = {
    "jd": 9247,
    "xiaohongshu": 9251,
    "pdd": 9223,
}

DEFAULT_PORT: int = DEFAULT_CDP_PORT

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
        """Extract cookies for *platform* from the browser on *port*,
        and save them to disk atomically.

        Only saves when required login-signal cookies are present.
        If the necessary cookies are not found, returns without overwriting.

        Args:
            platform: Platform name — one of:
                      ``taobao``, ``xiaohongshu``, ``zhihu``, ``zsxq``,
                      ``jd``, ``pdd``, ``weibo``, ``douyin``.
            port:     CDP debug port the browser is listening on.
                      Defaults per platform (jd→9247, xiaohongshu→9251,
                      pdd→9223, others→9222).

        Returns:
            ``{platform, count, saved_to, status, cookies?}``

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
        raw = asyncio.run(self._harvest_raw(platform, port, domain))
        return self._save_cookies(platform, raw)

    def harvest_raw(self, platform: str, port: int | None = None) -> dict[str, str]:
        """Extract cookies WITHOUT saving to disk — for polling/inspection.

        Returns the raw cookie dict {name: value} without touching the
        filesystem.  Use this when you need to check cookie contents
        before deciding whether to persist.

        Args:
            platform: Platform name.
            port:     CDP debug port (default per platform).

        Returns:
            ``{cookie_name: cookie_value, ...}`` — empty dict if none found.
        """
        if platform not in PLATFORM_DOMAINS:
            return {}
        if port is None:
            port = DEFAULT_PORTS.get(platform, DEFAULT_PORT)
        domain = PLATFORM_DOMAINS[platform]
        return asyncio.run(self._harvest_raw(platform, port, domain))

    # ── async internals ──────────────────────────────────────

    async def _harvest_raw(
        self,
        platform: str,
        port: int,
        domain: str,
    ) -> dict[str, str]:
        """Raw cookie extraction — returns dict without saving to disk."""
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
        except (TimeoutError, OSError) as e:
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

        return cookie_dict

    def _save_cookies(self, platform: str, cookie_dict: dict[str, str]) -> dict:
        """Atomically save cookies to disk and return result dict.

        Only writes if the platform's login-signal cookie(s) are present.
        This prevents anonymous browser cookies from overwriting valid
        login credentials on disk.  guided_login() performs the same
        check before calling here; this gate protects direct harvest().
        """
        if not cookie_dict:
            logger.warning(
                "Zero cookies harvested for %s. Existing cookie file preserved.",
                platform,
            )
            return {
                "platform": platform,
                "count": 0,
                "saved_to": None,
                "status": "empty",
                "hint": f"未找到 {platform} 的 Cookie。请确认浏览器已登录。",
            }

        # Gate: require at least one login-signal cookie before overwriting
        signal_cookies = _get_signal_cookies(platform)
        if signal_cookies:
            if not any(sc in cookie_dict for sc in signal_cookies):
                missing = [sc for sc in signal_cookies if sc not in cookie_dict]
                logger.warning(
                    "Harvested %d cookies for %s but none of the login-signal "
                    "cookies %s are present. Existing cookie file preserved.",
                    len(cookie_dict), platform, signal_cookies,
                )
                return {
                    "platform": platform,
                    "count": len(cookie_dict),
                    "saved_to": None,
                    "status": "partial",
                    "hint": (
                        f"收到了 {len(cookie_dict)} 个 {platform} Cookie，"
                        f"但缺少登录凭证（{', '.join(missing)}）。\n"
                        "请确认浏览器已登录并重试。"
                    ),
                }

        # Atomic save: write to temp file, then replace
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
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            resp = json.loads(raw)
            if resp.get("id") == mid:
                if "error" in resp:
                    raise CookieHarvestError(
                        f"CDP error: {resp['error'].get('message', str(resp['error']))}"
                    )
                return resp.get("result", {})


# ── Guided login: launch browser → user logs in → auto-harvest ─────

# Required cookies that signal successful login for each platform
# _LOGIN_SIGNAL_COOKIES is loaded from session.get_login_signal_cookies()
def _get_signal_cookies(platform: str) -> list[str]:
    """Resolve login-signal cookies for a platform (imported from session module)."""
    return get_login_signal_cookies(platform)


# Login page URLs for each platform
_LOGIN_URLS: dict[str, str] = {
    "taobao": "https://login.taobao.com/member/login.jhtml",
    "xiaohongshu": "https://www.xiaohongshu.com/login",
    "zhihu": "https://www.zhihu.com/signin",
    "zsxq": "https://wx.zsxq.com/",
    "jd": "https://passport.jd.com/new/login.aspx",
    "weibo": "https://weibo.com/login.php",
    "douyin": "https://www.douyin.com/",
    "pdd": "https://mobile.yangkeduo.com/login.html",
}

# Port defaults per platform for guided_login
_GUIDED_LOGIN_PORTS: dict[str, int] = {
    "jd": 9247,
}

# how long to wait for the user to log in
GUIDED_LOGIN_TIMEOUT = 120  # seconds
GUIDED_LOGIN_POLL = 3       # seconds between polls


def guided_login(platform: str, port: int | None = None, timeout: int = GUIDED_LOGIN_TIMEOUT) -> dict:
    """Launch a browser, wait for user to log in, then harvest cookies.

    Opens Chrome on the platform's login page.  You scan the QR code or
    enter credentials in the browser window.  As soon as login is detected
    (required cookies appear), cookies are harvested and saved.

    Platform-specific behaviour:
    - Most platforms: polls via CDP → saves cookie JSON on login.
    - JD (京东): uses persistent Chrome profile (~/.jd_login_profile).
      No cookie JSON is saved — JDEngine reads the profile directly.
      Success requires thor/TrackID cookies in the profile session.

    Args:
        platform: Platform name (taobao/xiaohongshu/zhihu/zsxq/jd/weibo/pdd/douyin)
        port: CDP debug port (default per platform, or 9222)
        timeout: Max seconds to wait for login (default 120)

    Returns:
        {platform, count, saved_to, status, method: "guided_login"}
    """
    import time as _time

    from cn_scraper_mcp.engines.cdp import _is_our_port

    if platform not in PLATFORM_DOMAINS:
        raise ValueError(
            f"Unsupported platform '{platform}'. Must be one of: "
            f"{', '.join(sorted(PLATFORM_DOMAINS.keys()))}"
        )

    domain = PLATFORM_DOMAINS[platform]
    login_url = _LOGIN_URLS.get(platform, f"https://{domain.lstrip('.')}")
    signal_cookies = _get_signal_cookies(platform)

    if port is None:
        port = _GUIDED_LOGIN_PORTS.get(platform, DEFAULT_PORT)

    # ── 1. Launch Chrome ──────────────────────────────────

    # Use session module for profile path resolution
    temp_profile = str(get_profile_dir(platform))

    # Only close a browser on this port if WE launched it.
    # NEVER touch the user's personal Chrome — close_browser()
    # is already safe, but we add an explicit check for clarity.
    if is_chrome_running(port):
        if _is_our_port(port):
            logger.info(
                "guided_login: closing our managed Chrome on port %d before re-launch", port,
            )
            close_browser(port)
            _time.sleep(1)
        else:
            logger.info(
                "guided_login: port %d is in use by another Chrome instance — "
                "not closing (not ours)", port,
            )

    logger.info(
        "guided_login: 正在为 %s 启动浏览器... (平台=%s 端口=%d 超时=%ds)",
        platform, platform, port, timeout,
    )

    try:
        proc = launch_chrome(port, temp_profile, url=login_url, headless=False)
    except RuntimeError as e:
        logger.error("guided_login: Chrome launch failed for %s on port %d: %s", platform, port, e)
        return {
            "platform": platform,
            "count": 0,
            "saved_to": None,
            "status": "error",
            "method": "guided_login",
            "hint": (
                f"无法启动 Chrome — 端口 {port} 已被其他进程占用。\n"
                "该端口可能被您自己的 Chrome 浏览器占用。\n"
                "请关闭占用该端口的 Chrome 后重试，或指定不同的端口。\n"
                f"错误详情: {e}"
            ),
        }

    if proc is None:
        logger.error("guided_login: Chrome launch failed for %s on port %d", platform, port)
        return {
            "platform": platform,
            "count": 0,
            "saved_to": None,
            "status": "error",
            "method": "guided_login",
            "hint": (
                f"Chrome 启动失败。请确认 Chrome 已安装，且端口 {port} 未被占用。\n"
                "可设置 CHROME_PATH 环境变量指向 Chrome 可执行文件。"
            ),
        }

    # ── 2. Poll for login cookies via CDP (read-only, no save) ──
    harvester = CookieHarvester()
    deadline = _time.monotonic() + timeout

    logger.info(
        "⏳ 等待登录: 平台=%s 端口=%d 超时=%ds 登录凭据=%s",
        platform, port, timeout, signal_cookies,
    )
    logger.info("📋 请在打开的浏览器窗口中完成 %s 的登录（扫码或输入密码）", platform)

    last_logged_remaining: int = -1
    poll_count: int = 0

    while _time.monotonic() < deadline:
        _time.sleep(GUIDED_LOGIN_POLL)
        poll_count += 1

        remaining = max(0, int(deadline - _time.monotonic()))
        # Log remaining time every ~15 seconds to avoid spam
        if remaining > 0 and (last_logged_remaining < 0 or remaining <= last_logged_remaining - 15):
            logger.info("⏳ 等待登录中... 平台=%s 端口=%d 剩余时间≈%ds", platform, port, remaining)
            last_logged_remaining = remaining

        try:
            raw = harvester.harvest_raw(platform, port=port)
        except CookieHarvestError:
            if poll_count <= 2:
                logger.info("🔌 CDP 连接尚未就绪 (端口=%d)，继续等待...", port)
            continue  # CDP not ready yet

        if not raw:
            continue

        # Check if required login-signal cookies are present
        if any(sc in raw for sc in signal_cookies):
            logger.info(
                "✅ 检测到登录成功！平台=%s 发现信号 Cookie: %s",
                platform,
                [sc for sc in signal_cookies if sc in raw],
            )

            # For profile-based platforms (JD), just confirm — no JSON to save
            if is_profile_platform(platform):
                return {
                    "platform": platform,
                    "count": len(raw),
                    "saved_to": str(temp_profile),
                    "status": "ok",
                    "method": "guided_login",
                    "hint": (
                        f"京东登录成功。登录态保存在 Chrome profile: {temp_profile}。\n"
                        "JDEngine 会直接使用该 profile——无需额外的 cookie 文件。\n"
                        "现在可以调用 jd_search 进行京东搜索。"
                    ),
                }

            # Save and return for cookie-based platforms
            result = harvester._save_cookies(platform, raw)
            result["method"] = "guided_login"
            return result

    # ── 3. Timeout ────────────────────────────────────────
    logger.warning("⏰ 登录超时: 平台=%s 已等待 %ds", platform, timeout)
    return {
        "platform": platform,
        "count": 0,
        "saved_to": None,
        "status": "timeout",
        "method": "guided_login",
        "hint": (
            f"登录超时 ({timeout}秒)。请确认已在浏览器中完成 {platform} 的登录。\n"
            f"浏览器窗口仍开着，可以手动登录后再试 harvest_cookies。"
        ),
    }
