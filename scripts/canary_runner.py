#!/usr/bin/env python
"""Canary test runner for cn-scraper-mcp.

Runs deterministic mock smoke queries for each platform.  This runner does
not claim to monitor live platform availability; live canaries require
dedicated accounts and credentials.

On failure the runner saves sanitised diagnostics (no cookies, no full
response bodies) and prints a GitHub Issue template when the consecutive
failure count exceeds the per-platform threshold.

Usage:
    python scripts/canary_runner.py --platform weibo
    python scripts/canary_runner.py --all --json
    python scripts/canary_runner.py --all --max-failures 5
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_FILE = Path(
    os.environ.get(
        "CN_SCRAPER_CANARY_STATE_FILE",
        Path.home() / ".cn-scraper-mcp" / "canary-state.json",
    )
)

# ═══════════════════════════════════════════════════════════════
# Platform config
# ═══════════════════════════════════════════════════════════════

_QueryFn = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class CanaryConfig:
    key: str
    label: str
    frequency_minutes: int
    timeout_seconds: float
    max_failures: int
    query_fn: _QueryFn


# ── Mock query implementations ──────────────────────────────────


def _mock_taobao() -> dict[str, Any]:
    return {"platform": "taobao", "query": "手机壳", "items": 20, "mock": True}


def _mock_jd() -> dict[str, Any]:
    return {"platform": "jd", "query": "笔记本电脑", "items": 15, "mock": True}


def _mock_pdd() -> dict[str, Any]:
    return {"platform": "pdd", "query": "蓝牙耳机", "items": 10, "mock": True}


def _mock_xiaohongshu() -> dict[str, Any]:
    return {"platform": "xiaohongshu", "query": "防晒霜推荐", "notes": 8, "mock": True}


def _mock_zhihu() -> dict[str, Any]:
    return {"platform": "zhihu", "query": "Python", "answers": 12, "mock": True}


def _mock_zsxq() -> dict[str, Any]:
    return {"platform": "zsxq", "group_id": "28888555451", "topics": 5, "mock": True}


def _mock_weibo() -> dict[str, Any]:
    return {"platform": "weibo", "query": "热搜", "posts": 10, "mock": True}


def _mock_douyin() -> dict[str, Any]:
    return {"platform": "douyin", "hot_list": True, "items": 20, "mock": True}


PLATFORM_CONFIGS: list[CanaryConfig] = [
    CanaryConfig("taobao", "Taobao / Tmall", 30, 10.0, 3, _mock_taobao),
    CanaryConfig("jd", "JD (京东)", 30, 15.0, 3, _mock_jd),
    CanaryConfig("pdd", "Pinduoduo (拼多多)", 30, 15.0, 3, _mock_pdd),
    CanaryConfig("xiaohongshu", "Xiaohongshu (小红书)", 30, 15.0, 3, _mock_xiaohongshu),
    CanaryConfig("zhihu", "Zhihu (知乎)", 30, 10.0, 3, _mock_zhihu),
    CanaryConfig("zsxq", "ZSXQ (知识星球)", 30, 10.0, 3, _mock_zsxq),
    CanaryConfig("weibo", "Weibo (微博)", 30, 10.0, 3, _mock_weibo),
    CanaryConfig("douyin", "Douyin (抖音)", 30, 10.0, 3, _mock_douyin),
]

PLATFORM_MAP: dict[str, CanaryConfig] = {c.key: c for c in PLATFORM_CONFIGS}

# ═══════════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════════


@dataclass
class CanaryResult:
    platform: str
    ok: bool
    duration_ms: float
    error_type: str = ""
    error_message: str = ""
    error_traceback: str = ""
    status_code: int | None = None
    timestamp: str = ""


@dataclass
class RunReport:
    results: list[CanaryResult] = field(default_factory=list)
    total_platforms: int = 0
    passed: int = 0
    failed: int = 0
    alerts: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# Sanitiser — strip sensitive fields
# ═══════════════════════════════════════════════════════════════

_SENSITIVE_KEYS = {
    "cookie", "cookies", "token", "access_token", "authorization",
    "api_key", "secret", "password", "session", "set-cookie",
    "x-csrf-token", "xsrf", "csrf",
}


def sanitise(data: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy with sensitive keys redacted."""
    result: dict[str, Any] = {}
    for k, v in data.items():
        k_lower = k.lower().replace("_", "-")
        if any(needle in k_lower for needle in _SENSITIVE_KEYS):
            result[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > 200:
            result[k] = v[:200] + "...[TRUNCATED]"
        else:
            result[k] = v
    return result


def build_diagnostics(
    result: CanaryResult,
    cfg: CanaryConfig,
    trace: str = "",
) -> dict[str, Any]:
    """Build a sanitised diagnostic payload for a failed canary run."""
    diag: dict[str, Any] = {
        "timestamp": result.timestamp or datetime.now(UTC).isoformat(),
        "platform": result.platform,
        "label": cfg.label,
        "ok": False,
        "duration_ms": result.duration_ms,
        "error_type": result.error_type,
        "error_message": result.error_message,
        "status_code": result.status_code,
        "consecutive_failures": 0,
        "trace_snippet": trace[:500] if trace else "",
    }
    return sanitise(diag)


# ═══════════════════════════════════════════════════════════════
# GitHub Issue template
# ═══════════════════════════════════════════════════════════════


def format_issue_template(
    cfg: CanaryConfig,
    consecutive: int,
    diag: dict[str, Any],
) -> str:
    """Return a GitHub Issue body template for a failing canary."""
    return (
        f"## Canary Alert: {cfg.label} ({cfg.key})\n\n"
        f"- **Platform**: {cfg.key}\n"
        f"- **Consecutive failures**: {consecutive}/{cfg.max_failures}\n"
        f"- **Last failure**: {diag.get('timestamp', 'unknown')}\n"
        f"- **Error type**: {diag.get('error_type', 'unknown')}\n"
        f"- **Error message**: {diag.get('error_message', 'n/a')}\n"
        f"- **Status code**: {diag.get('status_code', 'n/a')}\n"
        f"- **Duration**: {diag['duration_ms']:.0f} ms\n\n"
        f"### Suggested actions\n\n"
        f"1. Check if the platform is reachable from CI.\n"
        f"2. Verify credentials / cookies are up to date.\n"
        f"3. Review recent code changes to {cfg.key} engine.\n"
        f"4. If transient, increase `max_failures` for this platform.\n\n"
        f"### Raw diagnostics (sanitised)\n\n"
        f"```json\n{json.dumps(diag, ensure_ascii=False, indent=2)}\n```\n"
    )


# ═══════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════

# Per-platform consecutive failure counters. CLI runs persist these so a
# fresh process can continue counting failures from the previous invocation.
_consecutive_failures: dict[str, int] = {}


def _load_failure_counters(path: Path) -> None:
    """Load persisted counters, treating missing/corrupt state as empty."""
    _consecutive_failures.clear()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _consecutive_failures.update(
                {str(key): int(value) for key, value in raw.items() if int(value) >= 0}
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return


def _save_failure_counters(path: Path) -> None:
    """Atomically persist counters for the next process invocation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_consecutive_failures, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _clear_failure_counter(platform: str) -> None:
    _consecutive_failures[platform] = 0


def _record_failure(platform: str) -> int:
    _consecutive_failures[platform] = _consecutive_failures.get(platform, 0) + 1
    return _consecutive_failures[platform]


def run_canary(cfg: CanaryConfig) -> CanaryResult:
    """Execute a single canary query and return the result."""
    t0 = time.perf_counter()
    error_type = ""
    error_message = ""
    error_traceback = ""
    status_code: int | None = None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(cfg.query_fn)
            result = future.result(timeout=cfg.timeout_seconds)
        if not isinstance(result, dict):
            return CanaryResult(
                platform=cfg.key,
                ok=False,
                duration_ms=(time.perf_counter() - t0) * 1000,
                error_type="MalformedResponse",
                error_message=f"Expected dict, got {type(result).__name__}",
                timestamp=datetime.now(UTC).isoformat(),
            )
        return CanaryResult(
            platform=cfg.key,
            ok=True,
            duration_ms=(time.perf_counter() - t0) * 1000,
            timestamp=datetime.now(UTC).isoformat(),
        )
    except TimeoutError as e:
        error_type = "Timeout"
        error_message = str(e) or "Query timed out"
        error_traceback = traceback.format_exc()
    except ConnectionError as e:
        error_type = "ConnectionError"
        error_message = str(e)
        error_traceback = traceback.format_exc()
    except OSError as e:
        error_type = "OSError"
        error_message = str(e)
        error_traceback = traceback.format_exc()
    except Exception as e:
        error_type = type(e).__name__
        error_message = str(e)
        error_traceback = traceback.format_exc()

    return CanaryResult(
        platform=cfg.key,
        ok=False,
        duration_ms=(time.perf_counter() - t0) * 1000,
        error_type=error_type,
        error_message=error_message,
        error_traceback=error_traceback,
        status_code=status_code,
        timestamp=datetime.now(UTC).isoformat(),
    )


def run_all(
    platforms: list[str] | None = None,
    state_file: Path | str | None = None,
) -> RunReport:
    """Run canary queries for the given platforms (or all if None)."""
    state_path = Path(state_file) if state_file is not None else None
    if state_path is not None:
        _load_failure_counters(state_path)
    configs = PLATFORM_CONFIGS
    if platforms:
        configs = [PLATFORM_MAP[p] for p in platforms]

    report = RunReport(total_platforms=len(configs))

    for cfg in configs:
        result = run_canary(cfg)
        report.results.append(result)

        if result.ok:
            _clear_failure_counter(cfg.key)
            report.passed += 1
        else:
            report.failed += 1
            consecutive = _record_failure(cfg.key)

            diag = build_diagnostics(result, cfg, result.error_traceback)
            diag["consecutive_failures"] = consecutive

            if consecutive >= cfg.max_failures:
                issue = format_issue_template(cfg, consecutive, diag)
                report.alerts.append(issue)

    if state_path is not None:
        _save_failure_counters(state_path)
    return report


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="cn-scraper-mcp canary test runner",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--platform",
        choices=list(PLATFORM_MAP.keys()),
        help="Run canary for a single platform",
    )
    group.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Run canary for all platforms",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of a summary",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="Persist consecutive failure counts across invocations",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.platform:
        targets = [args.platform]
    elif args.all:
        targets = [c.key for c in PLATFORM_CONFIGS]
    else:
        # Default: all platforms
        targets = [c.key for c in PLATFORM_CONFIGS]

    report = run_all(targets, state_file=args.state_file)

    if args.json:
        output: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "total": report.total_platforms,
            "passed": report.passed,
            "failed": report.failed,
            "alerts": len(report.alerts),
            "results": [
                {
                    "platform": r.platform,
                    "ok": r.ok,
                    "duration_ms": round(r.duration_ms, 2),
                    "error_type": r.error_type or None,
                    "error_message": r.error_message or None,
                    "timestamp": r.timestamp,
                }
                for r in report.results
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for r in report.results:
            icon = "PASS" if r.ok else "FAIL"
            extra = ""
            if not r.ok:
                extra = f"  [{r.error_type}] {r.error_message}"
            print(f"  {icon}  {r.platform:<16}  {r.duration_ms:7.1f}ms{extra}")

        print(f"\n{report.passed}/{report.total_platforms} passed, {report.failed} failed")

        if report.alerts:
            print(f"\n{'=' * 60}")
            print(f"  ALERTS ({len(report.alerts)} platform(s) exceeded max_failures)")
            print(f"{'=' * 60}")
            for issue in report.alerts:
                print(issue)

    return 1 if report.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
