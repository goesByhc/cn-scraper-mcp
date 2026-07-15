"""SessionManager — unified session management for cn-scraper-mcp.

Three session types:
  - CookieSession: manages ~/.cn-scraper-cookies/<platform>.json files
  - ChromeProfileSession: manages persistent Chrome profiles (e.g. ~/.jd_login_profile)
  - CDPSession: wraps CDP BrowserLock, port management, process management

Usage::

    from cn_scraper_mcp.session import SessionManager

    mgr = SessionManager()
    status = mgr.validate("taobao")       # → {valid: True, reason: ""}
    mgr.login("taobao")                   # launch guided login
    mgr.delete("taobao")                  # remove cookie file
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import time as _time
from pathlib import Path
from typing import Any

from cn_scraper_mcp.auth import PLATFORM_CONFIG, STALE_HOURS
from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.session")

# ═══════════════════════════════════════════════════════════════
# Shared constants
# ═══════════════════════════════════════════════════════════════

COOKIE_DIR: Path = Path.home() / ".cn-scraper-cookies"
"""Hardcoded save directory for security — never user-overridable."""

JD_PROFILE_DIR: Path = Path.home() / ".jd_login_profile"
"""Default Chrome profile directory for JD persistent login."""

# Platform → session type mapping
_PLATFORM_SESSION_TYPE: dict[str, str] = {
    "jd": "chrome_profile",
}

# Platforms that use persistent Chrome profile instead of cookie JSON
_PROFILE_PLATFORMS: frozenset[str] = frozenset({"jd"})

# Login signal cookies per platform (for detecting successful login)
_LOGIN_SIGNAL_COOKIES: dict[str, list[str]] = {
    "taobao": ["_m_h5_tk"],
    "xiaohongshu": ["web_session"],
    "zhihu": ["z_c0"],
    "zsxq": ["zsxq_access_token"],
    "jd": ["thor", "TrackID"],
    "weibo": ["SUB"],
    "douyin": ["sessionid"],
    "pdd": ["PDDAccessToken"],
}

# Default CDP debug ports per platform
_DEFAULT_PORTS: dict[str, int] = {
    "jd": 9247,
    "xiaohongshu": 9251,
    "pdd": 9223,
}

DEFAULT_CDP_PORT: int = 9222


def _get_default_port(platform: str) -> int:
    """Return the default CDP debug port for a platform."""
    return _DEFAULT_PORTS.get(platform, DEFAULT_CDP_PORT)


# ═══════════════════════════════════════════════════════════════
# CookieSession
# ═══════════════════════════════════════════════════════════════


class CookieSession:
    """Manage a per-platform cookie JSON file session.

    Resolves the file path in this order:
      1. Custom path passed to __init__
      2. Platform-specific env var (e.g. TAOBAO_COOKIES_FILE)
      3. ~/.cn-scraper-cookies/<filename>.json

    Validates required cookie keys per platform.
    NEVER logs or returns cookie values — only field names.
    """

    def __init__(
        self,
        platform: str,
        cookies_path: str | None = None,
        port: int | None = None,
    ) -> None:
        if platform not in PLATFORM_CONFIG:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(PLATFORM_CONFIG))}"
            )
        self.platform = platform
        self.config = PLATFORM_CONFIG[platform]
        self._cookies_path = cookies_path
        self._port = port

    # ── path resolution ──────────────────────────────────

    def resolve_path(self) -> Path:
        """Resolve the cookie file path using the priority chain."""
        if self._cookies_path:
            return Path(self._cookies_path).expanduser().resolve()

        env = self.config["env_var"]
        if env in os.environ:
            return Path(os.environ[env]).expanduser().resolve()

        return COOKIE_DIR / self.config["filename"]

    @property
    def cookie_file(self) -> Path:
        """Convenience alias for resolve_path()."""
        return self.resolve_path()

    # ── validation ───────────────────────────────────────

    def _validate_fields(self, data: dict) -> list[str]:
        """Return the list of REQUIRED field names that are missing from *data*."""
        missing = []
        for field in self.config["required_fields"]:
            if field not in data or data[field] is None or data[field] == "":
                missing.append(field)
        return missing

    def _read_cookies(self) -> dict | None:
        """Read cookie file and return parsed dict, or None on failure."""
        path = self.resolve_path()
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def validate(self) -> dict:
        """Check if cookie session is valid.

        Returns:
            {valid: bool, reason: str, path: str, mtime: str | None,
             age_hours: float | None, stale: bool}
        """
        path = self.resolve_path()

        if not path.exists():
            return {
                "valid": False,
                "reason": f"Cookie file not found: {path}",
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        data = self._read_cookies()
        if data is None:
            return {
                "valid": False,
                "reason": f"Cookie file unreadable or invalid JSON: {path}",
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        missing = self._validate_fields(data)
        if missing:
            return {
                "valid": False,
                "reason": f"Missing required fields: {', '.join(missing)}",
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        stat = path.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
        stale = age_h > STALE_HOURS

        return {
            "valid": not stale,
            "reason": f"Cookie file is stale ({age_h:.1f}h old)" if stale else "",
            "path": str(path),
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": stale,
        }

    # ── session lifecycle ────────────────────────────────

    def login(self) -> dict:
        """Launch guided login for this platform."""
        from cn_scraper_mcp.cookie_harvest import guided_login as _guided_login

        return _guided_login(self.platform, port=self._port)

    def refresh(self) -> dict:
        """Re-harvest cookies from the browser."""
        from cn_scraper_mcp.cookie_harvest import CookieHarvester, CookieHarvestError

        try:
            harvester = CookieHarvester()
            return harvester.harvest(self.platform, port=self._port)
        except CookieHarvestError as e:
            return {
                "platform": self.platform,
                "count": 0,
                "saved_to": None,
                "status": "error",
                "reason": str(e),
            }

    def status(self) -> dict:
        """Full status snapshot — alias for validate with extra fields."""
        result = self.validate()
        result["platform"] = self.platform
        result["session_type"] = "cookie"
        return result

    def delete(self) -> dict:
        """Delete the cookie file for this platform."""
        path = self.resolve_path()
        if path.exists():
            try:
                path.unlink()
                logger.info("Deleted cookie file: %s", path)
                return {
                    "platform": self.platform,
                    "deleted": True,
                    "path": str(path),
                }
            except OSError as e:
                return {
                    "platform": self.platform,
                    "deleted": False,
                    "path": str(path),
                    "reason": str(e),
                }
        return {
            "platform": self.platform,
            "deleted": False,
            "path": str(path),
            "reason": "File does not exist",
        }


# ═══════════════════════════════════════════════════════════════
# ChromeProfileSession
# ═══════════════════════════════════════════════════════════════


class ChromeProfileSession:
    """Manage a persistent Chrome profile session.

    Used by JD (京东) which requires a persistent logged-in Chrome profile
    to bypass anti-bot detection. No cookie JSON is saved — JDEngine reads
    the profile directly.

    Default profile: ~/.jd_login_profile
    """

    def __init__(
        self,
        platform: str = "jd",
        profile_dir: str | None = None,
        port: int | None = None,
    ) -> None:
        self.platform = platform
        self._profile_dir = profile_dir
        self._port = port

    # ── profile resolution ───────────────────────────────

    def resolve_profile_dir(self) -> Path:
        """Resolve the profile directory path."""
        if self._profile_dir:
            return Path(self._profile_dir).expanduser().resolve()
        return JD_PROFILE_DIR

    @property
    def profile_dir(self) -> Path:
        """Convenience alias for resolve_profile_dir()."""
        return self.resolve_profile_dir()

    # ── validation ───────────────────────────────────────

    def validate(self) -> dict:
        """Check if the Chrome profile is valid.

        Returns:
            {valid: bool, reason: str, path: str, mtime: str | None,
             age_hours: float | None, stale: bool}
        """
        profile = self.resolve_profile_dir()
        exists = profile.exists() and profile.is_dir()

        if not exists:
            return {
                "valid": False,
                "reason": f"Chrome profile not found: {profile}",
                "path": str(profile),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        stat = profile.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
        stale = age_h > STALE_HOURS

        return {
            "valid": not stale,
            "reason": f"Chrome profile is stale ({age_h:.1f}h old)" if stale else "",
            "path": str(profile),
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": stale,
        }

    # ── session lifecycle ────────────────────────────────

    def login(self) -> dict:
        """Launch guided login for this platform using Chrome profile."""
        from cn_scraper_mcp.cookie_harvest import guided_login as _guided_login

        return _guided_login(self.platform, port=self._port)

    def refresh(self) -> dict:
        """Re-login (re-launch guided login)."""
        return self.login()

    def status(self) -> dict:
        """Full status snapshot."""
        result = self.validate()
        result["platform"] = self.platform
        result["session_type"] = "chrome_profile"
        return result

    def delete(self) -> dict:
        """Close the browser and remove the Chrome profile directory."""
        import shutil

        profile = self.resolve_profile_dir()
        port = self._port or _get_default_port(self.platform)

        # Close browser if running on this port
        from cn_scraper_mcp.engines.cdp import close_browser, is_chrome_running

        if is_chrome_running(port):
            close_browser(port)

        if profile.exists():
            try:
                shutil.rmtree(profile, ignore_errors=True)
                logger.info("Deleted Chrome profile: %s", profile)
                return {
                    "platform": self.platform,
                    "deleted": True,
                    "path": str(profile),
                }
            except OSError as e:
                return {
                    "platform": self.platform,
                    "deleted": False,
                    "path": str(profile),
                    "reason": str(e),
                }
        return {
            "platform": self.platform,
            "deleted": False,
            "path": str(profile),
            "reason": "Profile directory does not exist",
        }


# ═══════════════════════════════════════════════════════════════
# CDPSession
# ═══════════════════════════════════════════════════════════════


class CDPSession:
    """Manage a CDP browser session on a specific port.

    Wraps BrowserLock, port management, and process management from cdp.py.
    Records last_success and latency_ms for observability.
    """

    def __init__(self, port: int = DEFAULT_CDP_PORT) -> None:
        self.port = port
        self._last_success: float | None = None
        self._latency_ms: float | None = None
        self._lock_holder: bool = False

    # ── port / process management ────────────────────────

    @property
    def is_running(self) -> bool:
        """Check if Chrome is listening on this session's port."""
        from cn_scraper_mcp.engines.cdp import is_chrome_running

        return is_chrome_running(self.port)

    def get_lock(self) -> threading.Lock:
        """Get the per-port threading.Lock for concurrency control."""
        from cn_scraper_mcp.engines.cdp import get_browser_lock

        return get_browser_lock(self.port)

    def launch(self, profile_dir: str, url: str = "about:blank", headless: bool = False) -> Any:
        """Launch Chrome on this session's port.

        Returns:
            subprocess.Popen handle on success, None on failure.
        """
        from cn_scraper_mcp.engines.cdp import launch_chrome

        result = launch_chrome(self.port, profile_dir, url=url, headless=headless)
        if result is not None:
            self._lock_holder = True
        return result

    def close(self) -> bool:
        """Close the browser on this session's port."""
        from cn_scraper_mcp.engines.cdp import close_browser

        result = close_browser(self.port)
        self._lock_holder = False
        return result

    # ── metrics ──────────────────────────────────────────

    def record_success(self, latency_ms: float | None = None) -> None:
        """Record a successful CDP operation."""
        self._last_success = _time.monotonic()
        if latency_ms is not None:
            self._latency_ms = latency_ms

    @property
    def last_success(self) -> float | None:
        """Timestamp of the last successful CDP operation (monotonic)."""
        return self._last_success

    @property
    def latency_ms(self) -> float | None:
        """Latency of the last CDP operation in milliseconds."""
        return self._latency_ms

    # ── session interface ────────────────────────────────

    def validate(self) -> dict:
        """Check if the CDP session is valid (Chrome running on port).

        Returns:
            {valid: bool, reason: str}
        """
        running = self.is_running
        if running:
            return {"valid": True, "reason": ""}
        return {
            "valid": False,
            "reason": f"No Chrome/Obscura listening on port {self.port}",
        }

    def refresh(self) -> dict:
        """Restart the browser on this session's port."""
        self.close()
        _time.sleep(1)
        # Re-launch is not automatic — caller must provide profile and URL
        return {"port": self.port, "restarted": True}

    def status(self) -> dict:
        """Full status snapshot."""
        return {
            "valid": self.is_running,
            "port": self.port,
            "last_success": self._last_success,
            "latency_ms": self._latency_ms,
            "lock_holder": self._lock_holder,
        }

    def delete(self) -> dict:
        """Close the browser on this port."""
        closed = self.close()
        return {
            "port": self.port,
            "deleted": closed,
            "reason": "" if closed else "No managed process on this port",
        }


# ═══════════════════════════════════════════════════════════════
# SessionManager — unified top-level interface
# ═══════════════════════════════════════════════════════════════


class SessionManager:
    """Unified session manager routing platforms to their session type.

    Determines the session type per platform:
      - CookieSession for most platforms (taobao, xiaohongshu, zhihu, ...)
      - ChromeProfileSession for JD
      - CDPSession for raw CDP port management

    Usage::

        mgr = SessionManager()

        # Check all sessions
        mgr.validate("taobao")    # → {valid: True, reason: ""}
        mgr.status("jd")           # → {valid: True, session_type: "chrome_profile", ...}

        # Lifecycle
        mgr.login("taobao")        # guided login → harvest cookies
        mgr.delete("taobao")       # remove cookie file
    """

    def __init__(self) -> None:
        self._sessions: dict[str, CookieSession | ChromeProfileSession | CDPSession] = {}

    # ── session resolution ───────────────────────────────

    def _get_session_type(self, platform: str) -> str:
        """Determine which session type a platform uses."""
        return _PLATFORM_SESSION_TYPE.get(platform, "cookie")

    def _get_session(self, platform: str) -> CookieSession | ChromeProfileSession:
        """Get or create the session object for a platform."""
        if platform not in self._sessions:
            stype = self._get_session_type(platform)
            if stype == "chrome_profile":
                self._sessions[platform] = ChromeProfileSession(
                    platform=platform,
                    port=_get_default_port(platform),
                )
            elif stype == "cookie":
                self._sessions[platform] = CookieSession(
                    platform=platform,
                    port=_get_default_port(platform),
                )
            else:
                raise ValueError(
                    f"Unknown session type '{stype}' for platform '{platform}'"
                )
        return self._sessions[platform]

    def get_cdp_session(self, port: int = DEFAULT_CDP_PORT) -> CDPSession:
        """Get or create a CDPSession for raw port management.

        CDPSessions are keyed by port, not platform.
        """
        key = f"cdp:{port}"
        if key not in self._sessions:
            self._sessions[key] = CDPSession(port=port)
        return self._sessions[key]  # type: ignore[return-value]

    _ALL_PLATFORMS: tuple[str, ...] = tuple(
        sorted(set(PLATFORM_CONFIG.keys()) | {"jd"})
    )

    # ── public API ───────────────────────────────────────

    def login(self, platform: str) -> dict:
        """Launch guided login for *platform*.

        Opens a browser window and waits for the user to log in,
        then harvests cookies (or confirms profile login for JD).
        """
        return self._get_session(platform).login()

    def validate(self, platform: str) -> dict:
        """Check if the session for *platform* is valid.

        Returns:
            {valid: bool, reason: str}
        """
        return self._get_session(platform).validate()

    def refresh(self, platform: str) -> dict:
        """Refresh the session for *platform* (re-harvest cookies or re-login)."""
        return self._get_session(platform).refresh()

    def status(self, platform: str) -> dict:
        """Get full status for *platform*."""
        return self._get_session(platform).status()

    def delete(self, platform: str) -> dict:
        """Delete the session for *platform* (remove cookie file or profile dir)."""
        return self._get_session(platform).delete()

    def status_all(self) -> dict[str, dict]:
        """Get status for all known platforms at once."""
        result: dict[str, dict] = {}
        for platform in self._ALL_PLATFORMS:
            result[platform] = self.status(platform)
        return result

    def validate_all(self) -> dict[str, dict]:
        """Validate all known platforms at once."""
        result: dict[str, dict] = {}
        for platform in self._ALL_PLATFORMS:
            result[platform] = self.validate(platform)
        return result


# ═══════════════════════════════════════════════════════════════
# Convenience: path helpers for cookie_harvest / engines
# ═══════════════════════════════════════════════════════════════


def get_cookie_dir() -> Path:
    """Return the cookie storage directory (for external consumers)."""
    return COOKIE_DIR


def get_cookie_path(platform: str) -> Path:
    """Resolve the cookie file path for *platform* (respects env vars)."""
    session = CookieSession(platform)
    return session.resolve_path()


def get_profile_dir(platform: str = "jd") -> Path:
    """Resolve the Chrome profile directory for *platform*."""
    if platform == "jd":
        return JD_PROFILE_DIR
    return Path.home() / f".cn_scraper_login_{platform}"


def get_login_signal_cookies(platform: str) -> list[str]:
    """Return the login-signal cookies for *platform*."""
    return _LOGIN_SIGNAL_COOKIES.get(platform, [])


def is_profile_platform(platform: str) -> bool:
    """Return True if *platform* uses a persistent Chrome profile."""
    return platform in _PROFILE_PLATFORMS


__all__ = [
    "SessionManager",
    "CookieSession",
    "ChromeProfileSession",
    "CDPSession",
    "COOKIE_DIR",
    "JD_PROFILE_DIR",
    "DEFAULT_CDP_PORT",
    "get_cookie_dir",
    "get_cookie_path",
    "get_profile_dir",
    "get_login_signal_cookies",
    "is_profile_platform",
]
