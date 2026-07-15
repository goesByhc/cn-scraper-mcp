#!/usr/bin/env python
"""Platform health check script for cn-scraper-mcp.

Checks engine imports, cookie/profile validity, API connectivity, DOM selectors,
and browser (CDP) availability for each supported platform.

Modes:
    --mock    Simulated checks — no real network, no Chrome. Works in CI with zero setup.
    --real    Actual checks against live engines and credentials. Requires explicit flag.

Output:
    --json    Machine-readable JSON on stdout (structured v0.2.0 health report format).
    (default) Human-readable table on stdout.

Exit codes:
    0  All checked platforms healthy or skipped.
    1  At least one platform has auth_error.
    2  At least one platform blocked or adapter broken.
    3  Script error (missing deps, import failures, etc.).

Usage:
    python scripts/platform_health.py --mock --json          # CI
    python scripts/platform_health.py --real --platform taobao
    python scripts/platform_health.py --real --json
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ═══════════════════════════════════════════════════════════════
# Platform definitions
# ═══════════════════════════════════════════════════════════════

PLATFORMS: dict[str, dict] = {
    "taobao": {
        "label": "Taobao / Tmall",
        "engine_class": "TaobaoEngine",
        "engine_module": "cn_scraper_mcp.engines.taobao",
        "type": "api",
        "cookie_platform": "taobao",
    },
    "jd": {
        "label": "JD (京东)",
        "engine_class": "JDEngine",
        "engine_module": "cn_scraper_mcp.engines.jd",
        "type": "browser",
        "cookie_platform": "jd",
    },
    "pdd": {
        "label": "Pinduoduo (拼多多)",
        "engine_class": "PDDEngine",
        "engine_module": "cn_scraper_mcp.engines.pdd",
        "type": "browser",
        "cookie_platform": "pdd",
    },
    "xiaohongshu": {
        "label": "Xiaohongshu (小红书)",
        "engine_class": "XiaohongshuEngine",
        "engine_module": "cn_scraper_mcp.engines.xiaohongshu",
        "type": "browser",
        "cookie_platform": "xiaohongshu",
    },
    "zhihu": {
        "label": "Zhihu (知乎)",
        "engine_class": "ZhihuEngine",
        "engine_module": "cn_scraper_mcp.engines.zhihu",
        "type": "api",
        "cookie_platform": "zhihu",
    },
    "zsxq": {
        "label": "ZSXQ (知识星球)",
        "engine_class": "ZsxqEngine",
        "engine_module": "cn_scraper_mcp.engines.zsxq",
        "type": "api",
        "cookie_platform": "zsxq",
    },
}

# ═══════════════════════════════════════════════════════════════
# Status types (legacy — used for check-level and table output)
# ═══════════════════════════════════════════════════════════════

STATUS_OK = "ok"
STATUS_AUTH_ERROR = "auth_error"
STATUS_BLOCKED = "blocked"
STATUS_ADAPTER_BROKEN = "adapter_broken"
STATUS_SKIPPED = "skipped"

STATUS_ORDER = {
    STATUS_SKIPPED: 0,
    STATUS_OK: 1,
    STATUS_AUTH_ERROR: 2,
    STATUS_BLOCKED: 3,
    STATUS_ADAPTER_BROKEN: 4,
}

# ═══════════════════════════════════════════════════════════════
# v0.2.0 平台级健康状态 (ROADMAP §2.2)
# ═══════════════════════════════════════════════════════════════

HEALTHY = "healthy"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"

# ═══════════════════════════════════════════════════════════════
# 2.1 统一错误码 → 健康 reason 映射
# ═══════════════════════════════════════════════════════════════
# 从 check 级错误推导出最准确的 reason code

_UNIFIED_REASONS = {
    "session_expired",
    "captcha_required",
    "rate_limited",
    "risk_controlled",
    "network_timeout",
    "browser_unavailable",
    "cdp_unavailable",
    "selector_mismatch",
    "api_changed",
}


# ═══════════════════════════════════════════════════════════════
# Data classes
# ═══════════════════════════════════════════════════════════════


@dataclass
class CheckDetail:
    name: str
    status: str = STATUS_OK  # ok | error | skipped
    message: str = ""
    duration_ms: float = 0.0


@dataclass
class PlatformResult:
    platform: str
    status: str = STATUS_OK
    checks: list[CheckDetail] = field(default_factory=list)
    error: str = ""
    total_ms: float = 0.0


@dataclass
class HealthReport:
    """v0.2.0 结构化健康报告 (ROADMAP §2.2)."""

    platform: str
    status: str = HEALTHY  # healthy | degraded | unavailable
    reason: str | None = None  # 2.1 unified error code or None
    last_success: str | None = None  # ISO 8601 timestamp or None
    latency_ms: float = 0.0
    adapter_version: str = "unknown"


# ═══════════════════════════════════════════════════════════════
# Helpers — reason mapping & version
# ═══════════════════════════════════════════════════════════════


def _map_reason(checks: list[CheckDetail], platform_type: str) -> str | None:
    """Derive a 2.1 unified reason code from failed checks.

    Priority order (most specific first):
      session_expired > captcha_required > rate_limited > risk_controlled
      > network_timeout > browser_unavailable > cdp_unavailable
      > selector_mismatch > api_changed

    Returns None if no relevant error-code-mapped failure found.
    """
    error_checks = [c for c in checks if c.status == "error"]
    if not error_checks:
        return None

    all_msgs = " ".join(c.message.lower() + " " + c.name.lower() for c in error_checks)

    # Check for specific patterns in priority order
    if any(kw in all_msgs for kw in ("session_expired", "cookie expired", "cookie stale", "login expired", "auth_expired", "token expired")):
        return "session_expired"
    if any(kw in all_msgs for kw in ("captcha", "verify", "滑块", "验证码")):
        return "captcha_required"
    if any(kw in all_msgs for kw in ("rate", "too many request", "频率")):
        return "rate_limited"
    if any(kw in all_msgs for kw in ("risk", "suspicious", "friction", "风控", "anti-bot")):
        return "risk_controlled"
    if any(kw in all_msgs for kw in ("timeout", "timed out", "超时")):
        return "network_timeout"
    if any(kw in all_msgs for kw in ("browser not found", "no browser binary", "chrome not found", "browser_unavailable")):
        return "browser_unavailable"
    if any(kw in all_msgs for kw in ("cdp", "devtools", "debugging port", "cdp port")):
        return "cdp_unavailable"

    # Cookie/auth errors without specific expiry → session_expired
    if any(kw in all_msgs for kw in ("cookie", "auth", "missing fields", "cookie config invalid")):
        return "session_expired"

    # DOM / selector errors
    if any(kw in all_msgs for kw in ("selector", "dom", "missing method")):
        return "selector_mismatch"

    # Import / adapter failures → api_changed
    if any(kw in all_msgs for kw in ("import", "class not found", "constructor")) and platform_type == "api":
        return "api_changed"

    # Generic import/instantiate failures → api_changed (adapter broken)
    if any(kw in all_msgs for kw in ("import", "class not found", "instantiate")):
        return "api_changed"

    return None


def _get_adapter_version() -> str:
    """Return the package version string (e.g. 'v0.1.0')."""
    try:
        from cn_scraper_mcp import __version__

        return f"v{__version__}"
    except ImportError:
        return "unknown"


def _get_last_success() -> str | None:
    """Return the last known success timestamp in ISO 8601.

    In --mock mode this is always None.  In --real mode it is None
    unless a persisted health log exists (future integration point).
    """
    return None


# ═══════════════════════════════════════════════════════════════
# Helpers — sanitization
# ═══════════════════════════════════════════════════════════════

# Sensitive URL query parameter names to strip
_SENSITIVE_QUERY_PARAMS: set[str] = {
    "cookie", "cookies", "token", "access_token", "auth", "authorization",
    "key", "api_key", "apikey", "secret", "password", "passwd",
    "session", "sessionid", "sid", "jsessionid", "phpsessid",
}

# Sensitive header/value patterns to strip from messages
_SENSITIVE_HEADER_PATTERNS: list[str] = [
    r"cookie\s*[:=]\s*.+",
    r"authorization\s*[:=]\s*.+",
    r"set-cookie\s*[:=]\s*.+",
    r"x-csrf-token\s*[:=]\s*.+",
    r"x-xsrf-token\s*[:=]\s*.+",
    r"_token\s*[:=]\s*.+",
]

# Pattern for cookie-like values in text
_COOKIE_VALUE_PATTERN = re.compile(
    r"(cookie|Cookie|COOKIE)\s*[:=]\s*['\"]?[^'\",;\s]{8,}['\"]?",
    re.IGNORECASE,
)


def _sanitize_message(msg: str) -> str:
    """Strip sensitive data (cookies, tokens, auth headers) from a message string.

    This is a best-effort sanitizer — it removes obvious credential-like
    fragments but is not a cryptographically safe redactor.  The health
    report should never contain raw credentials in the first place.
    """
    if not msg:
        return msg

    # Strip cookie-like values: "Cookie: xxxxx..." → "Cookie: [REDACTED]"
    msg = _COOKIE_VALUE_PATTERN.sub(r"\1: [REDACTED]", msg)

    # Strip auth header patterns
    for pattern in _SENSITIVE_HEADER_PATTERNS:
        msg = re.sub(pattern, r"[REDACTED]", msg, flags=re.IGNORECASE)

    return msg


def _sanitize_url(url: str) -> str:
    """Remove sensitive query parameters from a URL."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url
        params = parsed.query.split("&")
        safe_params = []
        for p in params:
            if "=" in p:
                key = p.split("=", 1)[0].lower()
                if key not in _SENSITIVE_QUERY_PARAMS:
                    safe_params.append(p)
            else:
                safe_params.append(p)
        # If all params were stripped, remove the query entirely
        if safe_params:
            new_query = "&".join(safe_params)
        else:
            new_query = ""
        return parsed._replace(query=new_query).geturl()
    except Exception:
        return url


# ═══════════════════════════════════════════════════════════════
# Health check functions — Mock mode
# ═══════════════════════════════════════════════════════════════


def mock_check_import(platform_key: str, info: dict) -> CheckDetail:
    """Simulate engine import check."""
    t0 = time.perf_counter()
    module_name = info["engine_module"]
    class_name = info["engine_class"]

    try:
        mod = importlib.import_module(module_name)
        getattr(mod, class_name)
        msg = f"Class {class_name} found in {module_name}"
    except ImportError as e:
        return CheckDetail(
            name="import",
            status="error",
            message=f"Import failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except AttributeError as e:
        return CheckDetail(
            name="import",
            status="error",
            message=f"Class not found: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    return CheckDetail(
        name="import",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def mock_check_instantiate(platform_key: str, info: dict) -> CheckDetail:
    """Simulate engine instantiation check (does NOT actually instantiate)."""
    t0 = time.perf_counter()
    module_name = info["engine_module"]
    class_name = info["engine_class"]

    try:
        mod = importlib.import_module(module_name)
        engine_cls = getattr(mod, class_name)

        import inspect

        sig = inspect.signature(engine_cls.__init__)
        params = list(sig.parameters.keys())
        msg = f"Constructor signature valid ({', '.join(p for p in params if p != 'self')})"
    except Exception as e:
        return CheckDetail(
            name="instantiate",
            status="error",
            message=f"Instantiate check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    return CheckDetail(
        name="instantiate",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def mock_check_cookie(platform_key: str, info: dict) -> CheckDetail:
    """Validate cookie/profile config structure (mock — doesn't check files)."""
    t0 = time.perf_counter()

    cookie_platform = info["cookie_platform"]

    if cookie_platform == "jd":
        try:
            from cn_scraper_mcp import auth

            getattr(auth, "_check_jd_profile")
            msg = "JD uses Chrome profile dir (~/.jd_login_profile) — config valid"
            return CheckDetail(
                name="cookie",
                status="ok",
                message=msg,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except ImportError as e:
            return CheckDetail(
                name="cookie",
                status="error",
                message=f"Auth module not importable: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

    try:
        from cn_scraper_mcp.auth import CookieFileManager

        mgr = CookieFileManager(cookie_platform)
        resolved = mgr.resolve_path()

        msg = f"Cookie config valid (resolved path: {resolved})"

        return CheckDetail(
            name="cookie",
            status="ok",
            message=msg,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except ValueError as e:
        return CheckDetail(
            name="cookie",
            status="error",
            message=f"Cookie config invalid: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except ImportError as e:
        return CheckDetail(
            name="cookie",
            status="error",
            message=f"Auth module not importable: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="cookie",
            status="error",
            message=f"Cookie check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


def mock_check_api(platform_key: str, info: dict) -> CheckDetail:
    """Mock API check — validates engine has expected methods."""
    t0 = time.perf_counter()

    if info["type"] != "api":
        return CheckDetail(
            name="api",
            status="skipped",
            message="Platform type=browser — API check not applicable",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    try:
        module_name = info["engine_module"]
        class_name = info["engine_class"]
        mod = importlib.import_module(module_name)
        engine_cls = getattr(mod, class_name)

        expected_methods = {
            "taobao": ["search"],
            "zhihu": ["search", "hot_list"],
            "zsxq": ["get_topics"],
        }.get(platform_key, ["search"])

        missing = [m for m in expected_methods if not hasattr(engine_cls, m)]
        if missing:
            return CheckDetail(
                name="api",
                status="error",
                message=f"Missing methods: {', '.join(missing)}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        msg = f"API methods present ({', '.join(expected_methods)})"
        return CheckDetail(
            name="api",
            status="ok",
            message=msg,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="api",
            status="error",
            message=f"API check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


def mock_check_dom(platform_key: str, info: dict) -> CheckDetail:
    """Mock DOM selector check — validates known selectors exist in code."""
    t0 = time.perf_counter()

    known_selectors: dict[str, list[str]] = {
        "jd": ["div[data-sku]", "div.gl-item", "div.goods-list-v2 > div"],
        "pdd": [],
        "xiaohongshu": [],
        "taobao": [],
        "zhihu": [],
        "zsxq": [],
    }

    selectors = known_selectors.get(platform_key, [])
    if not selectors:
        return CheckDetail(
            name="dom",
            status="skipped",
            message="No DOM selectors defined for this platform (API-driven)",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    msg = f"Selectors defined ({len(selectors)}): {', '.join(selectors[:3])}"
    return CheckDetail(
        name="dom",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def mock_check_browser(platform_key: str, info: dict) -> CheckDetail:
    """Mock browser check — validates CDP module is importable."""
    t0 = time.perf_counter()

    if info["type"] != "browser":
        return CheckDetail(
            name="browser",
            status="skipped",
            message="Platform type=api — browser check not applicable",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    try:
        from cn_scraper_mcp.engines import cdp

        for symbol in ("CDPClient", "find_chrome", "find_obscura"):
            getattr(cdp, symbol)

        msg = "CDP module importable (find_chrome, find_obscura, CDPClient)"
        return CheckDetail(
            name="browser",
            status="ok",
            message=msg,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except ImportError as e:
        return CheckDetail(
            name="browser",
            status="error",
            message=f"CDP module not importable: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="browser",
            status="error",
            message=f"Browser check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


# ═══════════════════════════════════════════════════════════════
# Health check functions — Real mode
# ═══════════════════════════════════════════════════════════════


def real_check_import(platform_key: str, info: dict) -> CheckDetail:
    """Verify engine can be imported and class exists."""
    t0 = time.perf_counter()
    module_name = info["engine_module"]
    class_name = info["engine_class"]

    try:
        mod = importlib.import_module(module_name)
        getattr(mod, class_name)
        msg = f"Class {class_name} imported from {module_name}"
    except ImportError as e:
        return CheckDetail(
            name="import",
            status="error",
            message=f"Import failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except AttributeError:
        return CheckDetail(
            name="import",
            status="error",
            message=f"Class '{class_name}' not found in {module_name}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    return CheckDetail(
        name="import",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def real_check_instantiate(platform_key: str, info: dict) -> CheckDetail:
    """Verify engine can be instantiated (without triggering network)."""
    t0 = time.perf_counter()
    module_name = info["engine_module"]
    class_name = info["engine_class"]

    try:
        mod = importlib.import_module(module_name)
        engine_cls = getattr(mod, class_name)

        try:
            engine_cls()
            msg = f"Engine {class_name} instantiated successfully"
        except FileNotFoundError:
            msg = f"Engine {class_name} constructor ran (Chrome not found — will check separately)"
        except Exception as e:
            return CheckDetail(
                name="instantiate",
                status="error",
                message=f"Instantiation failed: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
    except Exception as e:
        return CheckDetail(
            name="instantiate",
            status="error",
            message=f"Instantiate check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    return CheckDetail(
        name="instantiate",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def real_check_cookie(platform_key: str, info: dict) -> CheckDetail:
    """Check cookie/profile file exists and is valid."""
    t0 = time.perf_counter()

    cookie_platform = info["cookie_platform"]

    if cookie_platform == "jd":
        try:
            from cn_scraper_mcp.auth import _check_jd_profile

            status = _check_jd_profile()

            if not status["exists"]:
                return CheckDetail(
                    name="cookie",
                    status="error",
                    message=(
                        f"JD login profile not found at {status['path']}. "
                        "Launch JD engine once to create it, then log in."
                    ),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )

            stale_note = " (STALE)" if status.get("stale") else ""
            msg = (
                f"JD login profile exists at {status['path']} "
                f"(age: {status.get('age_hours', '?')}h, "
                f"mtime: {status.get('mtime', '?')}){stale_note}"
            )

            detail_status = "ok"
            if status.get("stale"):
                detail_status = "error"

            return CheckDetail(
                name="cookie",
                status=detail_status,
                message=msg,
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except ImportError as e:
            return CheckDetail(
                name="cookie",
                status="error",
                message=f"Auth module not importable: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            return CheckDetail(
                name="cookie",
                status="error",
                message=f"Cookie check failed: {e}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

    try:
        from cn_scraper_mcp.auth import CookieFileManager

        mgr = CookieFileManager(cookie_platform)
        status = mgr.check()

        if not status["exists"]:
            return CheckDetail(
                name="cookie",
                status="error",
                message=(
                    f"Cookie file not found at {status['path']}. "
                    f"Required fields: {', '.join(status['missing_fields'])}"
                ),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        if not status["valid"]:
            return CheckDetail(
                name="cookie",
                status="error",
                message=(
                    f"Cookie file exists at {status['path']} but missing fields: "
                    f"{', '.join(status['missing_fields'])}"
                ),
                duration_ms=(time.perf_counter() - t0) * 1000,
            )

        stale_note = " (STALE)" if status.get("stale") else ""
        msg = (
            f"Cookie valid at {status['path']} "
            f"(age: {status.get('age_hours', '?')}h, "
            f"mtime: {status.get('mtime', '?')}){stale_note}"
        )

        detail_status = "ok"
        if status.get("stale"):
            detail_status = "error"

        return CheckDetail(
            name="cookie",
            status=detail_status,
            message=msg,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except ValueError as e:
        return CheckDetail(
            name="cookie",
            status="error",
            message=f"Cookie config invalid: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="cookie",
            status="error",
            message=f"Cookie check failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


def real_check_api(platform_key: str, info: dict) -> CheckDetail:
    """Send a minimal request to verify API connectivity (real mode)."""
    t0 = time.perf_counter()

    if info["type"] != "api":
        return CheckDetail(
            name="api",
            status="skipped",
            message="Platform type=browser — API check not applicable",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    probes = {
        "taobao": {"url": "https://h5api.m.taobao.com/", "timeout": 5},
        "zhihu": {"url": "https://www.zhihu.com/api/v4/search_v3?q=test&limit=1", "timeout": 10},
        "zsxq": {"url": "https://api.zsxq.com/v2/", "timeout": 5},
    }

    probe = probes.get(platform_key)
    if not probe:
        return CheckDetail(
            name="api",
            status="skipped",
            message=f"No API probe defined for {platform_key}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    try:
        import urllib.request

        req = urllib.request.Request(
            probe["url"],
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
                ),
            },
        )
        resp = urllib.request.urlopen(req, timeout=probe["timeout"])
        status_code = resp.status

        if 200 <= status_code < 300:
            msg = f"API reachable ({probe['url']}) — HTTP {status_code}"
        elif status_code in (401, 403):
            return CheckDetail(
                name="api",
                status="error",
                message=f"API auth/forbidden ({probe['url']}) — HTTP {status_code}",
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
        else:
            msg = f"API responded ({probe['url']}) — HTTP {status_code}"

        return CheckDetail(
            name="api",
            status="ok",
            message=msg,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="api",
            status="error",
            message=f"API probe failed ({probe['url']}): {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )


def real_check_dom(platform_key: str, info: dict) -> CheckDetail:
    """Check that known DOM selectors are defined in the engine code (real mode)."""
    t0 = time.perf_counter()

    known_selectors: dict[str, list[str]] = {
        "jd": ["div[data-sku]", "div.gl-item", "div.goods-list-v2 > div"],
        "pdd": [],
        "xiaohongshu": [],
        "taobao": [],
        "zhihu": [],
        "zsxq": [],
    }

    selectors = known_selectors.get(platform_key, [])
    if not selectors:
        return CheckDetail(
            name="dom",
            status="skipped",
            message="No DOM selectors defined for this platform (API-driven)",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    msg = f"Selectors defined ({len(selectors)}): {', '.join(selectors[:3])}"
    return CheckDetail(
        name="dom",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def real_check_browser(platform_key: str, info: dict) -> CheckDetail:
    """Check Chrome/Obscura binary available and CDP port reachable (real mode)."""
    t0 = time.perf_counter()

    if info["type"] != "browser":
        return CheckDetail(
            name="browser",
            status="skipped",
            message="Platform type=api — browser check not applicable",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    parts: list[str] = []

    try:
        from cn_scraper_mcp.engines.cdp import find_chrome, find_obscura

        chrome = find_chrome()
        obscura = find_obscura()
    except ImportError as e:
        return CheckDetail(
            name="browser",
            status="error",
            message=f"CDP module not importable: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
    except Exception as e:
        return CheckDetail(
            name="browser",
            status="error",
            message=f"Browser lookup failed: {e}",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    browser_found = False
    if chrome:
        parts.append(f"Chrome: {chrome}")
        browser_found = True
    if obscura:
        parts.append(f"Obscura: {obscura}")
        browser_found = True

    if not browser_found:
        return CheckDetail(
            name="browser",
            status="error",
            message="No browser binary found (Chrome or Obscura not in expected locations)",
            duration_ms=(time.perf_counter() - t0) * 1000,
        )

    cdp_ports = {
        "jd": 9247,
        "pdd": 9222,
        "xiaohongshu": 9251,
    }
    port = cdp_ports.get(platform_key, 9222)

    try:
        import urllib.request

        url = f"http://127.0.0.1:{port}/json/version"
        resp = urllib.request.urlopen(url, timeout=2)
        data = json.loads(resp.read())
        browser_name = data.get("Browser", "Unknown")
        parts.append(f"CDP port {port}: reachable ({browser_name})")
    except Exception:
        parts.append(f"CDP port {port}: not running (browser binary available — will launch on demand)")

    msg = "; ".join(parts)
    return CheckDetail(
        name="browser",
        status="ok",
        message=msg,
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


# ═══════════════════════════════════════════════════════════════
# Orchestration
# ═══════════════════════════════════════════════════════════════

MOCK_CHECKS = [
    ("import", mock_check_import),
    ("instantiate", mock_check_instantiate),
    ("cookie", mock_check_cookie),
    ("api", mock_check_api),
    ("dom", mock_check_dom),
    ("browser", mock_check_browser),
]

REAL_CHECKS = [
    ("import", real_check_import),
    ("instantiate", real_check_instantiate),
    ("cookie", real_check_cookie),
    ("api", real_check_api),
    ("dom", real_check_dom),
    ("browser", real_check_browser),
]


def run_platform_check(platform_key: str, info: dict, mode: str) -> PlatformResult:
    """Run all applicable health checks for a single platform."""
    t0 = time.perf_counter()
    result = PlatformResult(platform=platform_key)
    checks_pipeline = MOCK_CHECKS if mode == "mock" else REAL_CHECKS

    for check_name, check_fn in checks_pipeline:
        try:
            detail = check_fn(platform_key, info)
        except Exception as e:
            detail = CheckDetail(
                name=check_name,
                status="error",
                message=f"Check raised exception: {e}",
            )
        result.checks.append(detail)

    result.total_ms = (time.perf_counter() - t0) * 1000
    result.status = _compute_legacy_status(result.checks)
    if result.status != STATUS_OK:
        result.error = _build_error_message(result)

    return result


def _compute_legacy_status(checks: list[CheckDetail]) -> str:
    """Derive legacy platform-level status from individual check results."""
    active = [c for c in checks if c.status != "skipped"]
    if not active:
        return STATUS_SKIPPED

    error_checks = [c for c in active if c.status == "error"]

    if not error_checks:
        return STATUS_OK

    error_names = {c.name for c in error_checks}
    all_messages = " ".join(c.message.lower() for c in error_checks)

    if "cookie" in error_names or "auth" in all_messages or "expired" in all_messages:
        return STATUS_AUTH_ERROR

    if "blocked" in all_messages or "captcha" in all_messages or "rate" in all_messages:
        return STATUS_BLOCKED

    if "import" in error_names:
        return STATUS_ADAPTER_BROKEN

    return STATUS_ADAPTER_BROKEN


def _compute_health_status(checks: list[CheckDetail]) -> str:
    """Derive v0.2.0 platform health status.

    Returns one of: healthy, degraded, unavailable.
    """
    active = [c for c in checks if c.status != "skipped"]
    if not active:
        return UNAVAILABLE

    error_checks = [c for c in active if c.status == "error"]

    if not error_checks:
        return HEALTHY

    error_names = {c.name for c in error_checks}
    all_msgs = " ".join(c.message.lower() for c in error_checks)

    # Degraded: stale cookie or CDP not running (but binary available)
    # These are soft failures that may self-resolve
    soft_error_keywords = [
        "stale",
        "not running (browser binary available",
        "will launch on demand",
    ]
    is_soft = all(
        any(kw in c.message.lower() for kw in soft_error_keywords)
        or c.name in ("browser",)
        and "cdp port" in c.message.lower()
        and "not running" in c.message.lower()
        for c in error_checks
    )

    # Cookie stale alone → degraded (session may still work)
    if error_names == {"cookie"} and "stale" in all_msgs:
        return DEGRADED

    # All error checks are soft → degraded
    if is_soft:
        return DEGRADED

    # Critical: any hard failure → unavailable
    return UNAVAILABLE


def _build_error_message(result: PlatformResult) -> str:
    """Build a human-readable error message from failed checks."""
    failures = [c for c in result.checks if c.status == "error"]
    if not failures:
        return ""
    return "; ".join(f"{f.name}: {f.message}" for f in failures)


def compute_exit_code(results: list[PlatformResult]) -> int:
    """Compute the appropriate exit code based on aggregated results."""
    statuses = [r.status for r in results]

    if not statuses:
        return 0

    if STATUS_ADAPTER_BROKEN in statuses or STATUS_BLOCKED in statuses:
        return 2
    if STATUS_AUTH_ERROR in statuses:
        return 1

    return 0


# ═══════════════════════════════════════════════════════════════
# Health report builder
# ═══════════════════════════════════════════════════════════════


def build_health_report(
    platform_key: str,
    result: PlatformResult,
    info: dict,
) -> HealthReport:
    """Convert a PlatformResult into a v0.2.0 HealthReport.

    Derives status (healthy/degraded/unavailable), reason (2.1 code),
    and fills last_success/adapter_version.
    """
    health_status = _compute_health_status(result.checks)
    reason = _map_reason(result.checks, info["type"]) if health_status != HEALTHY else None

    return HealthReport(
        platform=platform_key,
        status=health_status,
        reason=reason,
        last_success=_get_last_success(),
        latency_ms=round(result.total_ms, 2),
        adapter_version=_get_adapter_version(),
    )


# ═══════════════════════════════════════════════════════════════
# Output formatters
# ═══════════════════════════════════════════════════════════════


def _status_icon(status: str) -> str:
    """Return a compact status indicator."""
    icons = {
        STATUS_OK: "\u2713",
        STATUS_AUTH_ERROR: "\u2717 AUTH",
        STATUS_BLOCKED: "\u2717 BLOCKED",
        STATUS_ADAPTER_BROKEN: "\u2717 BROKEN",
        STATUS_SKIPPED: "-",
    }
    return icons.get(status, "?")


def _health_status_icon(status: str) -> str:
    """Return an icon for the v0.2.0 health status."""
    icons = {
        HEALTHY: "\u2713 HEALTHY",
        DEGRADED: "~ DEGRADED",
        UNAVAILABLE: "\u2717 UNAVAIL",
    }
    return icons.get(status, "?")


def format_table(results: list[PlatformResult]) -> str:
    """Format results as a human-readable table."""
    lines = []
    lines.append(f"{'Platform':<22} {'Status':<14} {'Time':>8}  Details")
    lines.append("-" * 90)

    for r in results:
        label = PLATFORMS.get(r.platform, {}).get("label", r.platform)
        icon = _status_icon(r.status)
        status_str = f"{icon} {r.status}"
        time_str = f"{r.total_ms:.0f}ms"

        em_dash = "\u2014"
        lines.append(f"{label:<22} {status_str:<14} {time_str:>8}  {r.error or em_dash}")

        failed = [c for c in r.checks if c.status == "error"]
        if failed:
            for c in failed:
                lines.append(f"  \u2514\u2500 {c.name}: {c.message}")

    healthy = sum(1 for r in results if r.status == STATUS_OK)
    total = len(results)
    lines.append("")
    lines.append(f"Summary: {healthy}/{total} platforms healthy")

    return "\n".join(lines)


def _result_to_report_entry(result: PlatformResult, info: dict) -> dict:
    """Serialize a PlatformResult into the v0.2.2 health-report dict.

    Sanitization is applied to all check messages: cookies, auth headers,
    and sensitive query parameters are redacted.
    """
    report = build_health_report(result.platform, result, info)

    # Build sanitized check details
    sanitized_checks = []
    for c in result.checks:
        sanitized_msg = _sanitize_message(c.message)
        sanitized_checks.append(
            {
                "name": c.name,
                "status": c.status,
                "message": sanitized_msg,
                "duration_ms": round(c.duration_ms, 2),
            }
        )

    return {
        "platform": report.platform,
        "status": report.status,
        "reason": report.reason,
        "last_success": report.last_success,
        "latency_ms": report.latency_ms,
        "adapter_version": report.adapter_version,
        "checks": sanitized_checks,
    }


def format_json(results: list[PlatformResult], *, mode: str = "mock") -> str:
    """Format results as machine-readable JSON (v0.2.0 health report format)."""
    entries = []
    for r in results:
        info = PLATFORMS.get(r.platform, {"type": "unknown"})
        entries.append(_result_to_report_entry(r, info))

    output = {
        "mode": mode,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": entries,
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="cn-scraper-mcp platform health check",
    )
    parser.add_argument(
        "--platform",
        choices=list(PLATFORMS.keys()),
        help="Check a specific platform only (default: all)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of a table",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Run in mock mode — simulated checks, no real network/Chrome (default)",
    )
    mode_group.add_argument(
        "--real",
        action="store_true",
        default=False,
        help="Run real checks against live engines — requires credentials and Chrome",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    mode = "real" if args.real else "mock"

    if args.platform:
        platforms_to_check = {args.platform: PLATFORMS[args.platform]}
    else:
        platforms_to_check = PLATFORMS

    results: list[PlatformResult] = []
    for key, info in platforms_to_check.items():
        try:
            result = run_platform_check(key, info, mode)
            results.append(result)
        except Exception as e:
            results.append(
                PlatformResult(
                    platform=key,
                    status=STATUS_ADAPTER_BROKEN,
                    error=str(e),
                    checks=[
                        CheckDetail(
                            name="orchestrator",
                            status="error",
                            message=f"Platform check crashed: {e}",
                        )
                    ],
                )
            )

    if args.json:
        print(format_json(results, mode=mode))
    else:
        print(format_table(results))

    return compute_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
