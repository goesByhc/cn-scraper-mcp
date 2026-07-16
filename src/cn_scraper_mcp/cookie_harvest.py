"""CDP-based cookie auto-harvest module.

Uses Chrome DevTools Protocol (CDP) Network.getAllCookies to extract all
cookies — **including HttpOnly** cookies that are invisible to JavaScript.

This tool harvests cookies from the **user's own browser session**.  The
browser must already be running with --remote-debugging-port and the user
must already be logged into the target platform.  It does NOT steal cookies
or interact with sites the user hasn't explicitly logged into.

All platform configuration (domain, port, login URL, signal cookies) is read
from :class:`cn_scraper_mcp.auth.AuthProfile` — the single source of truth.

CDP transport (connect, commands, getAllCookies) is delegated to
:class:`cn_scraper_mcp.engines.cdp.CDPClient` — no raw websocket handling here.

Usage:
    from cn_scraper_mcp.cookie_harvest import CookieHarvester

    harvester = CookieHarvester()
    result = harvester.harvest("taobao", port=9222)
    # → {platform: "taobao", count: 17, saved_to: "~/.cn-scraper-cookies/taobao.json", status: "ok"}
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cn_scraper_mcp.auth import AUTH_PROFILES, DEFAULT_COOKIE_DIR
from cn_scraper_mcp.engines.cdp import CDPClient, close_browser, is_chrome_running, launch_chrome
from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.cookie_harvest")

# ── Constants ─────────────────────────────────────────────────────

DEFAULT_PORT: int = 9222
"""Default CDP port used when a platform's AuthProfile does not specify one."""

COOKIE_DIR: Path = DEFAULT_COOKIE_DIR
"""Hardcoded save directory for security — never user-overridable."""

_VALID_PLATFORMS: frozenset[str] = frozenset(AUTH_PROFILES.keys())
"""All supported platform names (from AuthProfile registry)."""

_PROFILE_PLATFORMS: frozenset[str] = frozenset(
    p for p, prof in AUTH_PROFILES.items() if prof.is_profile
)
"""Platforms that use persistent Chrome profile instead of cookie JSON."""


def _has_login_signal(
    cookie_dict: dict[str, str],
    signal_cookies: list[str] | tuple[str, ...],
) -> bool:
    """Return whether at least one login-signal cookie has a non-empty value."""
    return any(bool(cookie_dict.get(name)) for name in signal_cookies)

# ── Harvester ──────────────────────────────────────────────────────


class CookieHarvestError(Exception):
    """Cookie harvest failed — CDP connection, no page targets, or I/O error."""

    pass


class CookieHarvester:
    """Extract cookies from the user's own browser via Chrome DevTools Protocol.

    Connects to a running Chrome/Chromium instance (already launched with
    --remote-debugging-port), delegates CDP transport to :class:`CDPClient`,
    filters cookies for the target platform domain, and saves non-profile
    platforms to the JSON file configured by :class:`AuthProfile`.  Platforms
    backed by a persistent Chrome profile (currently JD) must use
    ``guided_login()`` and do not write a cookie JSON file.

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
        pass

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
                      Defaults from AuthProfile.login_port.

        Returns:
            ``{platform, count, saved_to, status, cookies?}``

        Raises:
            ValueError:       ``platform`` is not in the supported list.
            CookieHarvestError: CDP connection or protocol error.
        """
        profile = _get_profile(platform)

        if profile.is_profile:
            return {
                "platform": platform,
                "count": 0,
                "saved_to": None,
                "status": "profile_required",
                "hint": (
                    f"{platform} 使用持久化 Chrome profile，不使用 Cookie JSON 文件。"
                    f"请调用 guided_login('{platform}') 建立登录态。"
                ),
            }

        if port is None:
            port = profile.login_port or DEFAULT_PORT

        domain = profile.cookie_domain

        logger.info(
            "Harvesting cookies for platform=%s domain=%s port=%s",
            platform, domain, port,
        )
        raw = asyncio.run(self._harvest_raw(platform, port, domain))
        return self._save_cookies(platform, raw, profile)

    def harvest_raw(self, platform: str, port: int | None = None) -> dict[str, str]:
        """Extract cookies WITHOUT saving to disk — for polling/inspection.

        Returns the raw cookie dict {name: value} without touching the
        filesystem.  Use this when you need to check cookie contents
        before deciding whether to persist.

        Args:
            platform: Platform name.
            port:     CDP debug port (default from AuthProfile).

        Returns:
            ``{cookie_name: cookie_value, ...}`` — empty dict if none found.
        """
        try:
            profile = _get_profile(platform)
        except ValueError:
            return {}
        if port is None:
            port = profile.login_port or DEFAULT_PORT
        domain = profile.cookie_domain
        return asyncio.run(self._harvest_raw(platform, port, domain))

    # ── async internals ──────────────────────────────────────

    async def _harvest_raw(
        self,
        platform: str,
        port: int,
        domain: str,
    ) -> dict[str, str]:
        """Raw cookie extraction via CDPClient — returns dict without saving to disk."""
        cdp = CDPClient(port=port, timeout=15)
        try:
            await cdp.connect()
            return await cdp.get_all_cookies(domain=domain)
        except Exception as e:
            raise CookieHarvestError(
                f"CDP error on port {port}: {e}"
            ) from e
        finally:
            await cdp.close()


    def _save_cookies(
        self,
        platform: str,
        cookie_dict: dict[str, str],
        profile,
    ) -> dict:
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
        signal_cookies = list(profile.login_signal)
        if signal_cookies:
            if not _has_login_signal(cookie_dict, signal_cookies):
                missing = [sc for sc in signal_cookies if not cookie_dict.get(sc)]
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
        save_path = COOKIE_DIR / profile.cookie_filename
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

# ── Helpers ────────────────────────────────────────────────────────


def _get_profile(platform: str):
    """Look up the AuthProfile for *platform*, raising ValueError on miss."""
    if platform not in AUTH_PROFILES:
        raise ValueError(
            f"Unsupported platform '{platform}'. "
            f"Must be one of: {', '.join(sorted(_VALID_PLATFORMS))}"
        )
    return AUTH_PROFILES[platform]


# ── Guided login: launch browser → user logs in → auto-harvest ─────

# how long to wait for the user to log in
GUIDED_LOGIN_TIMEOUT = 120  # seconds
GUIDED_LOGIN_POLL = 3       # seconds between polls


def guided_login(
    platform: str,
    port: int | None = None,
    timeout: int = GUIDED_LOGIN_TIMEOUT,
) -> dict:
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
        port: CDP debug port (default from AuthProfile.login_port)
        timeout: Max seconds to wait for login (default 120)

    Returns:
        {platform, count, saved_to, status, method: "guided_login"}
    """
    import time as _time

    profile = _get_profile(platform)
    domain = profile.cookie_domain
    login_url = profile.login_url or f"https://{domain.lstrip('.')}"
    signal_cookies = list(profile.login_signal)

    if port is None:
        port = profile.login_port or DEFAULT_PORT

    # ── 1. Launch Chrome ──────────────────────────────────

    # JD uses its own persistent profile so JDEngine can find it
    if platform in _PROFILE_PLATFORMS:
        temp_profile = str(Path.home() / ".jd_login_profile")
    else:
        temp_profile = str(Path.home() / f".cn_scraper_login_{platform}")

    if is_chrome_running(port):
        close_browser(port)
        _time.sleep(1)

    logger.info(
        "guided_login: launching Chrome port=%d platform=%s profile=%s url=%s",
        port, platform, temp_profile, login_url,
    )

    proc = launch_chrome(port, temp_profile, url=login_url, headless=False)
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
        "guided_login: waiting for user to log in (signal=%s, timeout=%ds)...",
        signal_cookies, timeout,
    )

    while _time.monotonic() < deadline:
        _time.sleep(GUIDED_LOGIN_POLL)

        try:
            raw = harvester.harvest_raw(platform, port=port)
        except CookieHarvestError:
            continue  # CDP not ready yet

        if not raw:
            continue

        # Check if required login-signal cookies are present
        if _has_login_signal(raw, signal_cookies):
            logger.info(
                "guided_login: login detected for %s — found signal cookies",
                platform,
            )

            # For profile-based platforms (JD), just confirm — no JSON to save
            if platform in _PROFILE_PLATFORMS:
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
            result = harvester._save_cookies(platform, raw, profile)
            result["method"] = "guided_login"
            return result

    # ── 3. Timeout ────────────────────────────────────────
    logger.warning("guided_login: timeout after %ds for %s", timeout, platform)
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
