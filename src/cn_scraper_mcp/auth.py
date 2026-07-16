"""Unified cookie / credential management for cn-scraper-mcp.

Defines the single cross-platform business abstraction: authentication state.
Everything else — search, hot lists, comments, prices — remains platform-specific.

Types:
    AuthProfile          — frozen platform auth config (cookie file, env var, domain, …)
    CredentialCacheState — local cache check (missing / malformed / incomplete / stale / ready)

CookieFileManager
    Resolves cookie file path from env var or ~/.cn-scraper-cookies/<name>.json.
    Validates required cookie keys per platform.
    Context-manager for safe JSON reading.
    NEVER logs or returns cookie values — only field names.

check_all_cookies()
    Reports cache state per platform.  JD is a special case (Chrome profile dir).
"""

from __future__ import annotations

import datetime
import enum
import json
import os
from dataclasses import dataclass
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# AuthProfile — per-platform authentication configuration
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AuthProfile:
    """Per-platform authentication configuration.

    This is the ONLY cross-platform abstraction sanctioned by the architecture.
    It describes *how* to find and verify credentials — NOT what to do with them.

    Fields:
        platform:        Platform identifier (e.g. "taobao", "weibo").
        cookie_filename: JSON cookie filename in ~/.cn-scraper-cookies/.
        env_var:         Environment variable for custom cookie path.
        required_fields: Cookie keys that MUST be present for a valid cache.
        login_url:       Platform login page URL (used by guided_login).
        cookie_domain:   Cookie domain filter for CDP harvesting.
        login_port:      Default CDP port for this platform's login session.
        login_signal:    Cookies that signal a successful login (for guided_login).
        is_profile:      True if this platform uses a Chrome profile dir, not JSON.
    """

    platform: str
    cookie_filename: str
    env_var: str
    required_fields: tuple[str, ...] = ()
    login_url: str = ""
    cookie_domain: str = ""
    login_port: int = 9222
    login_signal: tuple[str, ...] = ()
    is_profile: bool = False


# ═══════════════════════════════════════════════════════════════
# Credential cache state — local file system checks
# ═══════════════════════════════════════════════════════════════


class CredentialCacheState(enum.StrEnum):
    """Result of a **local** cookie-file check.

    These states describe the *file on disk*, not the remote login session.
    A ``ready`` file may still be expired on the platform — ``check()`` does
    not perform remote verification.  The ``verified`` field in check() output
    is always ``false`` until a real platform-side check is implemented.

    Only the ``STALE`` state is a reliable signal: a file older than
    ``STALE_HOURS`` (currently 24 h) is almost certainly expired.
    """

    MISSING = "missing"          # File does not exist
    MALFORMED = "malformed"       # File exists but is not valid JSON
    INCOMPLETE = "incomplete"     # Valid JSON but missing required fields
    STALE = "stale"              # All fields present but file is too old
    READY = "ready"              # All fields present and file is fresh


# ═══════════════════════════════════════════════════════════════
# Auth profiles — single source of truth for all platforms
# ═══════════════════════════════════════════════════════════════

AUTH_PROFILES: dict[str, AuthProfile] = {
    "taobao": AuthProfile(
        platform="taobao",
        cookie_filename="taobao.json",
        env_var="TAOBAO_COOKIES_FILE",
        required_fields=("_m_h5_tk", "_tb_token_", "cookie2"),
        login_url="https://login.taobao.com/member/login.jhtml",
        cookie_domain=".taobao.com",
        login_port=9222,
        login_signal=("_m_h5_tk",),
    ),
    "xiaohongshu": AuthProfile(
        platform="xiaohongshu",
        cookie_filename="xiaohongshu.json",
        env_var="XHS_COOKIES_FILE",
        required_fields=("web_session", "a1"),
        login_url="https://www.xiaohongshu.com/login",
        cookie_domain=".xiaohongshu.com",
        login_port=9251,
        login_signal=("web_session",),
    ),
    "zhihu": AuthProfile(
        platform="zhihu",
        cookie_filename="zhihu.json",
        env_var="ZHIHU_COOKIES_FILE",
        required_fields=("z_c0",),
        login_url="https://www.zhihu.com/signin",
        cookie_domain=".zhihu.com",
        login_port=9222,
        login_signal=("z_c0",),
    ),
    "zsxq": AuthProfile(
        platform="zsxq",
        cookie_filename="zsxq.json",
        env_var="ZSXQ_COOKIES_FILE",
        required_fields=("zsxq_access_token",),
        login_url="https://wx.zsxq.com/",
        cookie_domain=".zsxq.com",
        login_port=9222,
        login_signal=("zsxq_access_token",),
    ),
    "pdd": AuthProfile(
        platform="pdd",
        cookie_filename="pdd.json",
        env_var="PDD_COOKIES_FILE",
        required_fields=("PDDAccessToken", "pdd_user_id"),
        login_url="https://mobile.yangkeduo.com/login.html",
        cookie_domain=".yangkeduo.com",
        login_port=9223,
        login_signal=("PDDAccessToken",),
    ),
    "weibo": AuthProfile(
        platform="weibo",
        cookie_filename="weibo.json",
        env_var="WEIBO_COOKIES_FILE",
        required_fields=("SUB",),
        login_url="https://weibo.com/login.php",
        cookie_domain=".weibo.com",
        login_port=9222,
        login_signal=("SUB",),
    ),
    "douyin": AuthProfile(
        platform="douyin",
        cookie_filename="douyin.json",
        env_var="DOUYIN_COOKIES_FILE",
        required_fields=(),
        login_url="https://www.douyin.com/",
        cookie_domain=".douyin.com",
        login_port=9222,
        login_signal=("sessionid",),
    ),
    "jd": AuthProfile(
        platform="jd",
        cookie_filename="",
        env_var="",
        required_fields=("thor", "TrackID"),
        login_url="https://passport.jd.com/new/login.aspx",
        cookie_domain=".jd.com",
        login_port=9247,
        login_signal=("thor", "TrackID"),
        is_profile=True,
    ),
}


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

STALE_HOURS = 24
# Most web sessions expire within 24 hours.  This is a LOCAL file-age gate —
# it does NOT verify that the remote platform still accepts the credentials.
# See the ``verified`` field in check() output for remote verification status.
DEFAULT_COOKIE_DIR: Path = Path.home() / ".cn-scraper-cookies"


# ═══════════════════════════════════════════════════════════════
# CookieFileManager
# ═══════════════════════════════════════════════════════════════


def _compute_cache_state(
    exists: bool,
    valid_json: bool,
    missing_fields: list[str],
    stale: bool,
) -> CredentialCacheState:
    """Derive a CredentialCacheState from raw check results."""
    if not exists:
        return CredentialCacheState.MISSING
    if not valid_json:
        return CredentialCacheState.MALFORMED
    if missing_fields:
        return CredentialCacheState.INCOMPLETE
    if stale:
        return CredentialCacheState.STALE
    return CredentialCacheState.READY


class CookieFileManager:
    """Manage a per-platform cookie JSON file.

    Resolves the file path in this order:
      1. Custom path passed to __init__
      2. Platform-specific env var (e.g. TAOBAO_COOKIES_FILE)
      3. ~/.cn-scraper-cookies/<filename>.json

    Validates that REQUIRED cookie keys are present.
    A context manager ensures the file handle is properly closed.

    Usage::

        with CookieFileManager("taobao") as mgr:
            print(mgr.status)  # {exists, valid, …, cache_state, …}

        # Or directly:
        mgr = CookieFileManager("taobao")
        print(mgr.check())     # same dict
    """

    def __init__(
        self,
        platform: str,
        cookies_path: str | None = None,
    ) -> None:
        if platform not in AUTH_PROFILES:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(k for k in AUTH_PROFILES if not AUTH_PROFILES[k].is_profile))}"
            )
        if AUTH_PROFILES[platform].is_profile:
            raise ValueError(
                f"Platform '{platform}' uses a Chrome profile, not a JSON cookie file. "
                f"Use the platform-specific engine directly."
            )
        self.platform = platform
        self.profile = AUTH_PROFILES[platform]
        self._cookies_path = cookies_path
        self._fh = None

    # ── path resolution ──────────────────────────────────

    def resolve_path(self) -> Path:
        """Resolve the cookie file path using the priority chain.

        Returns a Path object — does NOT check if the file exists.
        """
        if self._cookies_path:
            return Path(self._cookies_path).expanduser().resolve()

        env = self.profile.env_var
        if env and env in os.environ:
            return Path(os.environ[env]).expanduser().resolve()

        return DEFAULT_COOKIE_DIR / self.profile.cookie_filename

    # ── validation ───────────────────────────────────────

    def validate(self, data: dict) -> list[str]:
        """Return the list of REQUIRED field names that are missing from *data*.

        Cookie VALUES are never inspected beyond presence/absence.
        """
        missing = []
        for field in self.profile.required_fields:
            if field not in data or data[field] is None or data[field] == "":
                missing.append(field)
        return missing

    # ── status snapshot ──────────────────────────────────

    def check(self) -> dict:
        """Return a status dict for this platform's cookie file.

        This is a **local file audit** — it does NOT contact the platform
        to verify whether the cached credentials are still accepted remotely.
        The ``verified`` field is always ``false`` unless a remote login check
        has been performed.

        Returns::

            {
                "exists": bool,
                "valid": bool,
                "missing_fields": list[str],   # field names only — NO values
                "path": str | None,
                "mtime": str | None,           # ISO-8601
                "age_hours": float | None,
                "stale": bool,
                "cache_state": str,            # CredentialCacheState value
                "verified": bool,             # always false — local check only
            }
        """
        path = self.resolve_path()

        if not path.exists():
            return {
                "exists": False,
                "valid": False,
                "missing_fields": list(self.profile.required_fields),
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
                "cache_state": CredentialCacheState.MISSING.value,
                "verified": False,
            }

        # Read and validate
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            valid_json = True
        except (json.JSONDecodeError, OSError):
            valid_json = False
            return {
                "exists": True,
                "valid": False,
                "missing_fields": ["<file unreadable or invalid JSON>"],
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
                "cache_state": CredentialCacheState.MALFORMED.value,
                "verified": False,
            }

        # Reject valid JSON that is not a dict (e.g. null, array, scalar)
        if not isinstance(data, dict):
            return {
                "exists": True,
                "valid": False,
                "missing_fields": ["<JSON root is not an object>"],
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
                "cache_state": CredentialCacheState.MALFORMED.value,
                "verified": False,
            }

        missing = self.validate(data)

        stat = path.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
        stale = age_h > STALE_HOURS

        cache_state = _compute_cache_state(
            exists=True,
            valid_json=valid_json,
            missing_fields=missing,
            stale=stale,
        )

        return {
            "exists": True,
            "valid": len(missing) == 0,
            "missing_fields": missing,
            "path": str(path),
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": stale,
            "cache_state": cache_state.value,
            "verified": False,
        }

    # ── context manager ──────────────────────────────────

    def __enter__(self) -> CookieFileManager:
        path = self.resolve_path()
        if path.exists():
            self._fh = open(path, encoding="utf-8")
        return self

    def __exit__(self, *args) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    # ── properties ───────────────────────────────────────

    def load(self) -> dict:
        """Read and return the cookie dict without needing a context manager.

        Returns an empty dict if the file doesn't exist or is unreadable.
        Never raises — always returns a (possibly empty) dict.
        """
        path = self.resolve_path()
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    @property
    def data(self) -> dict | None:
        """Cookie dict (None if file doesn't exist or isn't opened via context manager).

        Prefer ``load()`` for one-shot reads outside a context manager.
        """
        if self._fh is None:
            return None
        self._fh.seek(0)
        try:
            data = json.load(self._fh)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    @property
    def status(self) -> dict:
        """Convenience alias for check()."""
        return self.check()


# ═══════════════════════════════════════════════════════════════
# check_all_cookies — replaces server._cookie_status()
# ═══════════════════════════════════════════════════════════════


def _check_jd_profile() -> dict:
    """Check JD Chrome login profile directory (special — not a JSON file)."""
    profile_dir = Path.home() / ".jd_login_profile"
    exists = profile_dir.exists() and profile_dir.is_dir()

    if exists:
        stat = profile_dir.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
        stale = age_h > STALE_HOURS
        cache_state = CredentialCacheState.STALE if stale else CredentialCacheState.READY
        return {
            "exists": True,
            "path": str(profile_dir),
            "type": "chrome_profile_dir",
            "valid": True,
            "missing_fields": [],
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": stale,
            "cache_state": cache_state.value,
            "verified": False,
        }

    return {
        "exists": False,
        "path": str(profile_dir),
        "type": "chrome_profile_dir",
        "valid": False,
        "missing_fields": [],
        "mtime": None,
        "age_hours": None,
        "stale": False,
        "cache_state": CredentialCacheState.MISSING.value,
        "verified": False,
    }


def check_all_cookies() -> dict:
    """Check cookie / credential status for all supported platforms.

    Returns a dict keyed by platform name.  Each value has the shape::

        {
            "exists": bool,
            "valid": bool,
            "missing_fields": list[str],
            "path": str | None,
            "mtime": str | None,        # ISO-8601
            "age_hours": float | None,
            "stale": bool,
            "cache_state": str,         # CredentialCacheState value
            "verified": bool,          # always false — local file audit only
        }

    JD is handled specially — it checks the Chrome profile directory
    (~/.jd_login_profile) instead of a JSON cookie file.

    NEVER returns cookie values — only field names and metadata.
    """
    result = {}

    for platform, profile in AUTH_PROFILES.items():
        if profile.is_profile:
            continue  # JD handled separately below
        mgr = CookieFileManager(platform)
        result[platform] = mgr.check()

    # JD special case
    result["jd"] = _check_jd_profile()

    return result


__all__ = [
    # Types
    "AuthProfile",
    "CredentialCacheState",
    # Auth profiles
    "AUTH_PROFILES",
    # Managers
    "CookieFileManager",
    "check_all_cookies",
    # Constants
    "STALE_HOURS",
    "DEFAULT_COOKIE_DIR",
]
