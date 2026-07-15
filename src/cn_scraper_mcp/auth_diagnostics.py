"""Auth diagnostics — automatic login problem diagnosis with actionable suggestions.

When a search fails, ``diagnose_auth_failure()`` analyses the error and returns:
  - ``diagnosis``: human-readable explanation
  - ``action``: suggested fix
  - ``suggest_guided_login``: whether ``guided_login()`` can resolve the issue

Usage::

    from cn_scraper_mcp.auth_diagnostics import diagnose_auth_failure, enrich_error_with_diagnostics

    diag = diagnose_auth_failure("taobao", {"error": {"code": "session_expired"}})
    # → {"diagnosis": "Cookie 已过期，需要重新登录", "action": "...", "suggest_guided_login": True}
"""

from __future__ import annotations

from typing import Any

# ═══════════════════════════════════════════════════════════════
# Diagnosis rule table: error_code → (diagnosis, action_template)
# ═══════════════════════════════════════════════════════════════

_DIAGNOSIS_RULES: dict[str, tuple[str, str]] = {
    # ── session / cookie ──────────────────────────────────
    "session_expired": (
        "Cookie 已过期，需要重新登录",
        "在浏览器中重新登录该平台，或使用 guided_login('{platform}') 自动收割新 Cookie。",
    ),
    "COOKIE_EXPIRED": (
        "Cookie 已过期，需要重新登录",
        "在浏览器中重新登录该平台，或使用 guided_login('{platform}') 自动收割新 Cookie。",
    ),
    "COOKIE_MISSING": (
        "Cookie 文件未找到，需要首次登录",
        "Cookie 文件不存在。请使用 guided_login('{platform}') 进行首次登录收割。",
    ),
    "AUTH_REQUIRED": (
        "需要认证但未提供有效凭据",
        "该平台需要登录。请使用 guided_login('{platform}') 完成登录。",
    ),
    # ── browser / CDP ─────────────────────────────────────
    "browser_unavailable": (
        "Chrome 未安装或未启动",
        "请安装 Chrome 浏览器，或设置 CHROME_PATH 环境变量指向 Chrome 可执行文件。",
    ),
    "BROWSER_ERROR": (
        "Chrome 未安装或未启动",
        "请安装 Chrome 浏览器，或设置 CHROME_PATH 环境变量指向 Chrome。",
    ),
    "cdp_unavailable": (
        "Chrome DevTools 连接失败",
        "请检查 Chrome 是否以 --remote-debugging-port 启动，且端口未被占用。",
    ),
    # ── captcha / risk ────────────────────────────────────
    "captcha_required": (
        "平台弹出验证码",
        "在浏览器中手动完成验证码后重试搜索。",
    ),
    "risk_controlled": (
        "IP 被风控",
        "当前 IP 被平台标记为风险。请更换网络（如切换 WiFi/热点）或等待风控解除后重试。",
    ),
    # ── rate / network ────────────────────────────────────
    "RATE_LIMITED": (
        "请求频率过高被限流",
        "请等待 3-5 分钟后重试。",
    ),
    "network_timeout": (
        "网络请求超时",
        "请检查网络连接后重试。如需 HTTP 代理，可设置 HTTPS_PROXY 环境变量。",
    ),
    # ── other ─────────────────────────────────────────────
    "INVALID_INPUT": (
        "输入参数无效",
        "请检查参数格式是否正确，详见工具文档。",
    ),
    "PLATFORM_ERROR": (
        "平台返回异常响应",
        "可能是平台临时故障或接口变更，请稍后重试。",
    ),
    "selector_mismatch": (
        "页面结构已变更",
        "平台可能更新了页面布局，抓取适配器需要更新。",
    ),
    "api_changed": (
        "平台 API 返回格式已变更",
        "平台接口已更新，抓取适配器需要升级。",
    ),
    "permission_denied": (
        "权限不足",
        "当前账号无权访问该内容，请确认账号有相应权限。",
    ),
}

# Error codes that can be resolved by guided_login
_GUIDED_LOGIN_FIXABLE: frozenset[str] = frozenset({
    "session_expired",
    "COOKIE_EXPIRED",
    "COOKIE_MISSING",
    "AUTH_REQUIRED",
})


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════


def diagnose_auth_failure(platform: str, error_dict: dict[str, Any]) -> dict[str, Any]:
    """Analyse an error response and return a diagnostic with actionable advice.

    Args:
        platform: Platform name (taobao, jd, xiaohongshu, ...).
        error_dict: The error dict from an MCP tool response, containing at minimum
                    ``{"error": {"code": "..."}}``.  Also accepts a ``ScraperError.to_dict()``
                    result or any dict with an ``"error"`` key.

    Returns:
        ``{"diagnosis": str, "action": str, "suggest_guided_login": bool}``
    """
    # Extract error code — handle both nested {"error": {"code": ...}} and flat {"code": ...}
    error_info: dict[str, Any] = {}
    if isinstance(error_dict, dict):
        error_info = error_dict.get("error", error_dict) if "error" in error_dict else error_dict
    if not isinstance(error_info, dict):
        error_info = {}

    code = error_info.get("code", "")

    if code and code in _DIAGNOSIS_RULES:
        diagnosis, action_template = _DIAGNOSIS_RULES[code]
        action = action_template.format(platform=platform)
    elif code:
        diagnosis = f"未知错误 (code={code})"
        action = "请检查服务端日志获取详细信息。"
    else:
        diagnosis = "无法识别的错误格式"
        action = "请检查服务端日志获取详细信息。"

    suggest_guided_login = code in _GUIDED_LOGIN_FIXABLE

    return {
        "diagnosis": diagnosis,
        "action": action,
        "suggest_guided_login": suggest_guided_login,
    }


def enrich_error_with_diagnostics(
    platform: str,
    error_response_dict: dict[str, Any],
) -> dict[str, Any]:
    """Augment an error response dict with diagnostic information.

    Modifies the ``hint`` field inside ``error_response_dict["error"]``
    to include a structured diagnostic block.  When the error is fixable
    via ``guided_login``, the hint will explicitly recommend it.

    Args:
        platform: Platform name.
        error_response_dict: A dict in the shape ``{"ok": False, "error": {...}}``
                             as produced by ``error_response()`` or ``ScraperError.to_dict()``.

    Returns:
        The same dict, with the hint enriched in-place.
    """
    diag = diagnose_auth_failure(platform, error_response_dict)

    # Build diagnostic line
    diag_parts = [f"诊断: {diag['diagnosis']}"]
    diag_parts.append(f"建议: {diag['action']}")
    if diag["suggest_guided_login"]:
        diag_parts.append(
            f"可执行: 调用 guided_login('{platform}') 自动打开浏览器完成登录。"
        )
    diag_block = "\n".join(diag_parts)

    # Append to existing hint
    error_block = error_response_dict.get("error", {})
    if isinstance(error_block, dict):
        original_hint = error_block.get("hint", "")
        if original_hint:
            error_block["hint"] = f"{original_hint}\n\n{diag_block}"
        else:
            error_block["hint"] = diag_block

    return error_response_dict


__all__ = [
    "diagnose_auth_failure",
    "enrich_error_with_diagnostics",
]
