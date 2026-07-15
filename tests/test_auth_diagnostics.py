"""Tests for auth_diagnostics.py — diagnosis, enrichment, and guided_login suggestions.

Covers:
  - diagnose_auth_failure() with all known error codes
  - Edge cases: missing code, non-dict input, empty dict
  - enrich_error_with_diagnostics() hint augmentation
  - suggest_guided_login flag for fixable errors
"""

from __future__ import annotations

import pytest

from cn_scraper_mcp.auth_diagnostics import (
    diagnose_auth_failure,
    enrich_error_with_diagnostics,
)

# ═══════════════════════════════════════════════════════════════
# diagnose_auth_failure — known error codes
# ═══════════════════════════════════════════════════════════════

KNOWN_DIAGNOSIS_CASES = [
    # (code, expected_diagnosis_keyword, expected_action_keyword, suggest_guided_login)
    ("session_expired", "Cookie", "guided_login", True),
    ("COOKIE_EXPIRED", "Cookie", "guided_login", True),
    ("COOKIE_MISSING", "Cookie 文件", "guided_login", True),
    ("AUTH_REQUIRED", "认证", "guided_login", True),
    ("browser_unavailable", "Chrome", "CHROME_PATH", False),
    ("BROWSER_ERROR", "Chrome", "CHROME_PATH", False),
    ("cdp_unavailable", "DevTools", "remote-debugging", False),
    ("captcha_required", "验证码", "完成验证码", False),
    ("risk_controlled", "风控", "IP", False),
    ("RATE_LIMITED", "限流", "重试", False),
    ("network_timeout", "超时", "网络", False),
    ("INVALID_INPUT", "参数", "格式", False),
    ("PLATFORM_ERROR", "异常", "稍后", False),
    ("selector_mismatch", "页面结构", "适配器", False),
    ("api_changed", "API", "适配器", False),
    ("permission_denied", "权限", "账号", False),
]


class TestDiagnoseAuthFailure:
    """diagnose_auth_failure() returns correct diagnosis/action/suggest_guided_login."""

    def test_input_with_error_wrapper(self):
        """Standard input shape: {"ok": False, "error": {"code": "..."}}."""
        error_dict = {"ok": False, "error": {"code": "session_expired", "message": "过期"}}
        result = diagnose_auth_failure("taobao", error_dict)
        assert "Cookie" in result["diagnosis"]
        assert "guided_login('taobao')" in result["action"]
        assert result["suggest_guided_login"] is True

    def test_input_with_flat_dict(self):
        """Flat dict input: {"code": "..."} without 'error' wrapper."""
        error_dict = {"code": "browser_unavailable"}
        result = diagnose_auth_failure("jd", error_dict)
        assert "Chrome" in result["diagnosis"]
        assert result["suggest_guided_login"] is False

    def test_input_scraper_error_to_dict_shape(self):
        """Input from ScraperError.to_dict() — nested 'error' key."""
        error_dict = {"ok": False, "error": {"code": "captcha_required", "retryable": True}}
        result = diagnose_auth_failure("xiaohongshu", error_dict)
        assert "验证码" in result["diagnosis"]
        assert result["suggest_guided_login"] is False

    @pytest.mark.parametrize(
        "code,diag_kw,action_kw,suggest_gl",
        KNOWN_DIAGNOSIS_CASES,
    )
    def test_known_error_codes(self, code, diag_kw, action_kw, suggest_gl):
        """Every known error code returns a relevant diagnosis."""
        error_dict = {"ok": False, "error": {"code": code}}
        result = diagnose_auth_failure("test_platform", error_dict)
        assert diag_kw in result["diagnosis"], (
            f"Expected '{diag_kw}' in diagnosis for code={code}, got '{result['diagnosis']}'"
        )
        assert action_kw in result["action"], (
            f"Expected '{action_kw}' in action for code={code}, got '{result['action']}'"
        )
        assert result["suggest_guided_login"] == suggest_gl, (
            f"suggest_guided_login expected {suggest_gl} for code={code}"
        )

    @pytest.mark.parametrize("platform", ["taobao", "jd", "xiaohongshu", "weibo", "zhihu"])
    def test_platform_is_substituted_in_action(self, platform):
        """The platform name is substituted into the action template."""
        error_dict = {"ok": False, "error": {"code": "session_expired"}}
        result = diagnose_auth_failure(platform, error_dict)
        assert f"guided_login('{platform}')" in result["action"]


# ═══════════════════════════════════════════════════════════════
# diagnose_auth_failure — edge cases
# ═══════════════════════════════════════════════════════════════


class TestDiagnoseEdgeCases:
    """Edge cases: missing code, non-dict input, empty dict."""

    def test_unknown_code(self):
        """An error code not in the rule table returns a generic diagnosis."""
        error_dict = {"ok": False, "error": {"code": "SOME_RANDOM_CODE"}}
        result = diagnose_auth_failure("taobao", error_dict)
        assert "未知错误" in result["diagnosis"]
        assert "SOME_RANDOM_CODE" in result["diagnosis"]
        assert result["suggest_guided_login"] is False

    def test_no_code_key(self):
        """If there's no 'code' key at all, return generic."""
        error_dict = {"ok": False, "error": {"message": "something broke"}}
        result = diagnose_auth_failure("taobao", error_dict)
        assert result["diagnosis"] != ""
        assert result["suggest_guided_login"] is False

    def test_empty_error_dict(self):
        """Empty dict shouldn't crash."""
        result = diagnose_auth_failure("taobao", {})
        assert isinstance(result["diagnosis"], str)
        assert isinstance(result["action"], str)
        assert result["suggest_guided_login"] is False

    def test_non_dict_input(self):
        """Non-dict input shouldn't crash."""
        result = diagnose_auth_failure("taobao", "not a dict")  # type: ignore[arg-type]
        assert isinstance(result["diagnosis"], str)
        assert result["suggest_guided_login"] is False

    def test_none_input(self):
        """None input shouldn't crash."""
        result = diagnose_auth_failure("taobao", None)  # type: ignore[arg-type]
        assert isinstance(result["diagnosis"], str)
        assert result["suggest_guided_login"] is False


# ═══════════════════════════════════════════════════════════════
# enrich_error_with_diagnostics
# ═══════════════════════════════════════════════════════════════


class TestEnrichErrorWithDiagnostics:
    """enrich_error_with_diagnostics augments the hint in error responses."""

    def test_adds_diagnosis_to_hint(self):
        """Diagnostic info is appended to the hint."""
        err_dict = {
            "ok": False,
            "error": {
                "code": "session_expired",
                "message": "登录过期",
                "hint": "请重新登录",
                "retryable": True,
            },
        }
        result = enrich_error_with_diagnostics("taobao", err_dict)
        hint = result["error"]["hint"]
        assert "诊断:" in hint
        assert "建议:" in hint
        assert "guided_login" in hint

    def test_adds_diagnosis_when_no_original_hint(self):
        """If there's no original hint, diagnosis becomes the hint."""
        err_dict = {
            "ok": False,
            "error": {
                "code": "COOKIE_MISSING",
                "message": "Cookie missing",
                "hint": "",
            },
        }
        result = enrich_error_with_diagnostics("jd", err_dict)
        hint = result["error"]["hint"]
        assert "诊断:" in hint
        assert "Cookie 文件" in hint

    def test_preserves_original_hint(self):
        """Original hint text is preserved before the diagnostic block."""
        err_dict = {
            "ok": False,
            "error": {
                "code": "captcha_required",
                "message": "验证码",
                "hint": "Original hint text",
                "retryable": True,
            },
        }
        result = enrich_error_with_diagnostics("xiaohongshu", err_dict)
        hint = result["error"]["hint"]
        assert hint.startswith("Original hint text")
        assert "诊断:" in hint

    def test_guided_login_suggestion_in_hint(self):
        """When suggest_guided_login is True, hint includes guided_login suggestion."""
        err_dict = {
            "ok": False,
            "error": {"code": "session_expired", "message": "过期", "hint": ""},
        }
        result = enrich_error_with_diagnostics("weibo", err_dict)
        hint = result["error"]["hint"]
        assert "guided_login('weibo')" in hint

    def test_no_guided_login_suggestion_for_non_fixable(self):
        """When suggest_guided_login is False, hint does NOT include guided_login."""
        err_dict = {
            "ok": False,
            "error": {"code": "risk_controlled", "message": "风控", "hint": ""},
        }
        result = enrich_error_with_diagnostics("taobao", err_dict)
        hint = result["error"]["hint"]
        assert "guided_login" not in hint

    def test_returns_same_dict_reference(self):
        """The function modifies and returns the same dict (in-place)."""
        err_dict = {
            "ok": False,
            "error": {"code": "browser_unavailable", "message": "browser", "hint": ""},
        }
        result = enrich_error_with_diagnostics("jd", err_dict)
        assert result is err_dict


# ═══════════════════════════════════════════════════════════════
# Integration: error response flow
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    """Full error-response-to-diagnostics flow."""

    def test_session_expired_full_flow(self):
        """Simulate a SessionExpiredError → error_response → enrich → final dict."""
        from cn_scraper_mcp.errors import SessionExpiredError, error_response

        exc = SessionExpiredError(
            message="小红书登录已过期",
            hint="Cookie 已失效，请重新登录。",
        )
        err_dict = error_response(exc)
        result = enrich_error_with_diagnostics("xiaohongshu", err_dict)

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"
        assert result["error"]["retryable"] is True
        hint = result["error"]["hint"]
        assert "Cookie 已失效" in hint  # original hint preserved
        assert "诊断:" in hint
        assert "guided_login('xiaohongshu')" in hint

    def test_browser_unavailable_full_flow(self):
        """Simulate a BrowserUnavailableError → error_response → enrich."""
        from cn_scraper_mcp.errors import BrowserUnavailableError, error_response

        exc = BrowserUnavailableError(
            message="Chrome 未找到",
            hint="请安装 Chrome 浏览器。",
        )
        err_dict = error_response(exc)
        result = enrich_error_with_diagnostics("jd", err_dict)

        hint = result["error"]["hint"]
        assert "Chrome" in hint
        assert "诊断:" in hint
        assert "guided_login" not in hint  # browser error not fixable by login

    def test_generic_exception_with_diagnostics(self):
        """Even a generic exception gets wrapped by error_response + diagnostics."""
        err_dict = {
            "ok": False,
            "error": {
                "code": "PLATFORM_ERROR",
                "message": "An unexpected error occurred",
                "hint": "Check the server logs for details.",
            },
        }
        result = enrich_error_with_diagnostics("pdd", err_dict)
        hint = result["error"]["hint"]
        assert "诊断:" in hint
        assert "平台" in hint


# ═══════════════════════════════════════════════════════════════
# Output structure
# ═══════════════════════════════════════════════════════════════


class TestOutputStructure:
    """Ensure diagnose_auth_failure always returns the 3 required keys."""

    def test_always_returns_three_keys(self):
        """Return value always has 'diagnosis', 'action', and 'suggest_guided_login'."""
        result = diagnose_auth_failure("taobao", {"error": {"code": "session_expired"}})
        assert set(result.keys()) == {"diagnosis", "action", "suggest_guided_login"}
        assert isinstance(result["diagnosis"], str)
        assert isinstance(result["action"], str)
        assert isinstance(result["suggest_guided_login"], bool)

    @pytest.mark.parametrize("code", ["session_expired", "risk_controlled", "network_timeout"])
    def test_diagnosis_is_never_empty(self, code):
        """The diagnosis string is never empty."""
        result = diagnose_auth_failure("taobao", {"error": {"code": code}})
        assert len(result["diagnosis"]) > 0

    @pytest.mark.parametrize("code", ["session_expired", "risk_controlled", "network_timeout"])
    def test_action_is_never_empty(self, code):
        """The action string is never empty."""
        result = diagnose_auth_failure("taobao", {"error": {"code": code}})
        assert len(result["action"]) > 0
