"""Unified cookie / credential management for cn-scraper-mcp.

CookieFileManager
    Resolves cookie file path from env var or ~/.cn-scraper-cookies/<name>.json.
    Validates required cookie keys per platform.
    Context-manager for safe JSON reading.
    NEVER logs or returns cookie values — only field names.

check_all_cookies()
    Drop-in replacement for the inline _cookie_status() in server.py.
    Reports file_exists, fields_valid, missing_required_fields, age_hours, stale
    per platform.  JD is a special case: it checks the Chrome profile dir
    (~/.jd_login_profile) rather than a JSON cookie file.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# Per-platform configuration
# ═══════════════════════════════════════════════════════════════

PLATFORM_CONFIG = {
    "taobao": {
        "filename": "taobao.json",
        "env_var": "TAOBAO_COOKIES_FILE",
        "required_fields": ["_m_h5_tk", "_tb_token_", "cookie2"],
    },
    "xiaohongshu": {
        "filename": "xiaohongshu.json",
        "env_var": "XHS_COOKIES_FILE",
        "required_fields": ["web_session", "a1"],
    },
    "zhihu": {
        "filename": "zhihu.json",
        "env_var": "ZHIHU_COOKIES_FILE",
        "required_fields": ["z_c0"],
    },
    "zsxq": {
        "filename": "zsxq.json",
        "env_var": "ZSXQ_COOKIES_FILE",
        "required_fields": ["zsxq_access_token"],
    },
    "pdd": {
        "filename": "pdd.json",
        "env_var": "PDD_COOKIES_FILE",
        "required_fields": ["PDDAccessToken", "pdd_user_id"],
    },
    "weibo": {
        "filename": "weibo.json",
        "env_var": "WEIBO_COOKIES_FILE",
        "required_fields": ["SUB"],
    },
    "douyin": {
        "filename": "douyin.json",
        "env_var": "DOUYIN_COOKIES_FILE",
        "required_fields": [],
    },
}

# Staleness threshold in hours — cookies older than this are considered stale.
STALE_HOURS = 72


# ═══════════════════════════════════════════════════════════════
# CookieFileManager
# ═══════════════════════════════════════════════════════════════

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
            print(mgr.status)  # {exists, valid, missing_fields, path, ...}

        # Or directly:
        mgr = CookieFileManager("taobao")
        print(mgr.check())     # same dict
    """

    def __init__(
        self,
        platform: str,
        cookies_path: str | None = None,
    ) -> None:
        if platform not in PLATFORM_CONFIG:
            raise ValueError(
                f"Unknown platform '{platform}'. "
                f"Must be one of: {', '.join(sorted(PLATFORM_CONFIG))}"
            )
        self.platform = platform
        self.config = PLATFORM_CONFIG[platform]
        self._cookies_path = cookies_path
        self._fh = None

    # ── path resolution ──────────────────────────────────

    def resolve_path(self) -> Path:
        """Resolve the cookie file path using the priority chain.

        Returns a Path object — does NOT check if the file exists.
        """
        if self._cookies_path:
            return Path(self._cookies_path).expanduser().resolve()

        env = self.config["env_var"]
        if env in os.environ:
            return Path(os.environ[env]).expanduser().resolve()

        return (
            Path.home() / ".cn-scraper-cookies" / self.config["filename"]
        )

    # ── validation ───────────────────────────────────────

    def validate(self, data: dict) -> list[str]:
        """Return the list of REQUIRED field names that are missing from *data*.

        Cookie VALUES are never inspected beyond presence/absence.
        """
        missing = []
        for field in self.config["required_fields"]:
            if field not in data or data[field] is None or data[field] == "":
                missing.append(field)
        return missing

    # ── status snapshot ──────────────────────────────────

    def check(self) -> dict:
        """Return a status dict for this platform's cookie file.

        Returns::

            {
                "exists": bool,
                "valid": bool,
                "missing_fields": list[str],   # field names only — NO values
                "path": str | None,
                "mtime": str | None,           # ISO-8601
                "age_hours": float | None,
                "stale": bool,
            }
        """
        path = self.resolve_path()

        if not path.exists():
            return {
                "exists": False,
                "valid": False,
                "missing_fields": self.config["required_fields"],
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        # Read and validate
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {
                "exists": True,
                "valid": False,
                "missing_fields": ["<file unreadable or invalid JSON>"],
                "path": str(path),
                "mtime": None,
                "age_hours": None,
                "stale": False,
            }

        missing = self.validate(data)

        stat = path.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600

        return {
            "exists": True,
            "valid": len(missing) == 0,
            "missing_fields": missing,
            "path": str(path),
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": age_h > STALE_HOURS,
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

    @property
    def data(self) -> dict | None:
        """Cookie dict (None if file doesn't exist or isn't opened)."""
        if self._fh is None:
            return None
        self._fh.seek(0)
        try:
            return json.load(self._fh)
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

    result: dict = {
        "exists": exists,
        "path": str(profile_dir),
        "type": "chrome_profile_dir",
        "valid": exists,
        "missing_fields": [],
    }

    if exists:
        stat = profile_dir.stat()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
        age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
        result.update({
            "mtime": mtime.isoformat(),
            "age_hours": round(age_h, 1),
            "stale": age_h > STALE_HOURS,
        })
    else:
        result.update({
            "mtime": None,
            "age_hours": None,
            "stale": False,
        })

    return result


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
        }

    JD is handled specially — it checks the Chrome profile directory
    (~/.jd_login_profile) instead of a JSON cookie file.

    NEVER returns cookie values — only field names and metadata.
    """
    result = {}

    for platform in PLATFORM_CONFIG:
        mgr = CookieFileManager(platform)
        result[platform] = mgr.check()

    # JD special case
    result["jd"] = _check_jd_profile()

    return result


def _check_legacy_file(filename: str) -> dict:
    """Check a cookie file without field validation (legacy / PDD)."""
    # Try new path first, then legacy path
    for base in (
        Path.home() / ".cn-scraper-cookies",
        Path.home() / "jd_scrape",
    ):
        path = base / filename
        if path.exists():
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            age_h = (datetime.datetime.now() - mtime).total_seconds() / 3600
            return {
                "exists": True,
                "valid": True,
                "missing_fields": [],
                "path": str(path),
                "mtime": mtime.isoformat(),
                "age_hours": round(age_h, 1),
                "stale": age_h > STALE_HOURS,
            }

    return {
        "exists": False,
        "valid": False,
        "missing_fields": [],
        "path": None,
        "mtime": None,
        "age_hours": None,
        "stale": False,
    }


__all__ = [
    "CookieFileManager",
    "check_all_cookies",
    "PLATFORM_CONFIG",
    "STALE_HOURS",
]
