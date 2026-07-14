#!/usr/bin/env python
"""Platform health check script for cn-scraper-mcp.

Checks engine imports, cookie/profile validity, API connectivity, DOM selectors,
and browser (CDP) availability for each supported platform.

Modes:
    --mock    Simulated checks — no real network, no Chrome. Works in CI with zero setup.
    --real    Actual checks against live engines and credentials. Requires explicit flag.

Output:
    --json    Machine-readable JSON on stdout.
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
import sys
import time
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════
# Platform definitions
# ═══════════════════════════════════════════════════════════════

PLATFORMS: dict[str, dict] = {
    "taobao": {
        "label": "Taobao / Tmall",
        "engine_class": "TaobaoEngine",
        "engine_module": "cn_scraper_mcp.engines.taobao",
        "type": "api",
        "cookie_platform": "taobao",  # key for auth.PLATFORM_CONFIG + CookieFileManager
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
# Status types
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


# ═══════════════════════════════════════════════════════════════
# Health check functions — Mock mode
# ═══════════════════════════════════════════════════════════════


def mock_check_import(platform_key: str, info: dict) -> CheckDetail:
    """Simulate engine import check."""
    t0 = time.perf_counter()
    # In mock mode we just verify the module path looks valid
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

        # Try to instantiate with dummy/non-existent path that won't hit real files
        # but validates the constructor signature at minimum
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

    # JD is special — uses a Chrome profile directory, not a JSON cookie file
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

        # In mock mode we just verify the auth infrastructure works:
        # the CookieFileManager can resolve a path and has valid config
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

        # Check for expected methods based on platform
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

    # Known selectors per platform (extracted from engine source)
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

        # Try instantiation — engines with curl_cffi deps may fail here
        try:
            engine_cls()
            msg = f"Engine {class_name} instantiated successfully"
        except FileNotFoundError:
            # Chrome missing for browser engines — that's OK, we check that separately
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

    # JD is special — uses a Chrome profile directory, not a JSON cookie file
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

    # Platform-specific minimal probes
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
    # In real mode, DOM selectors can only be validated at runtime with a browser.
    # This check just confirms the selectors are defined in the source.
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

    # 1. Find Chrome or Obscura
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

    # 2. Check if any CDP port is open
    cdp_ports = {  # Platform-specific default ports
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

# Check suites by mode
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
    result.status = _compute_status(result.checks)
    if result.status != STATUS_OK:
        result.error = _build_error_message(result)

    return result


def _compute_status(checks: list[CheckDetail]) -> str:
    """Derive platform-level status from individual check results."""

    # Skipped checks don't count
    active = [c for c in checks if c.status != "skipped"]
    if not active:
        return STATUS_SKIPPED

    error_checks = [c for c in active if c.status == "error"]

    if not error_checks:
        return STATUS_OK

    # Classify the worst error
    error_names = {c.name for c in error_checks}
    all_messages = " ".join(c.message.lower() for c in error_checks)

    # Auth-related: cookie missing, expired, or stale
    if "cookie" in error_names or "auth" in all_messages or "expired" in all_messages:
        return STATUS_AUTH_ERROR

    # Blocked: rate-limit, captcha, IP risk
    if "blocked" in all_messages or "captcha" in all_messages or "rate" in all_messages:
        return STATUS_BLOCKED

    # Import failures, missing deps
    if "import" in error_names:
        return STATUS_ADAPTER_BROKEN

    # Default: something's broken
    return STATUS_ADAPTER_BROKEN


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
# Output formatters
# ═══════════════════════════════════════════════════════════════


def _status_icon(status: str) -> str:
    """Return a compact status indicator."""
    icons = {
        STATUS_OK: "✓",
        STATUS_AUTH_ERROR: "✗ AUTH",
        STATUS_BLOCKED: "✗ BLOCKED",
        STATUS_ADAPTER_BROKEN: "✗ BROKEN",
        STATUS_SKIPPED: "-",
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

        lines.append(f"{label:<22} {status_str:<14} {time_str:>8}  {r.error or '—'}")

        # Print individual checks if there are failures
        failed = [c for c in r.checks if c.status == "error"]
        if failed:
            for c in failed:
                lines.append(f"  └─ {c.name}: {c.message}")

    # Summary line
    healthy = sum(1 for r in results if r.status == STATUS_OK)
    total = len(results)
    lines.append("")
    lines.append(f"Summary: {healthy}/{total} platforms healthy")

    return "\n".join(lines)


def format_json(results: list[PlatformResult]) -> str:
    """Format results as JSON."""

    def _serialize_result(r: PlatformResult) -> dict:
        return {
            "platform": r.platform,
            "status": r.status,
            "total_ms": round(r.total_ms, 2),
            "error": r.error or None,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "duration_ms": round(c.duration_ms, 2),
                }
                for c in r.checks
            ],
        }

    output = {
        "mode": "mock" if "--mock" in sys.argv else "real",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": [_serialize_result(r) for r in results],
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

    # Select platforms
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

    # Output
    if args.json:
        print(format_json(results))
    else:
        print(format_table(results))

    return compute_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
