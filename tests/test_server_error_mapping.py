"""Integration tests: engine exception → MCP tool error mapping (ROADMAP §2.1).

Tests both paths at each tool:
  1. Engine raises exception (caught by server try/except)
  2. Engine returns error dict (inspected by server post-processing)
then verifies the returned dict contains the expected error code.
"""

from unittest.mock import MagicMock, patch

from cn_scraper_mcp.cookie_harvest import CookieHarvestError

# Engine-specific exceptions
from cn_scraper_mcp.engines.jd import JDCaptchaError, JDLoginWallError
from cn_scraper_mcp.engines.pdd import PDDAuthError, PDDRateLimitError
from cn_scraper_mcp.engines.taobao import TaobaoAPIError, TaobaoAuthError

# Error classes
from cn_scraper_mcp.errors import (
    AuthRequiredError,
    PlatformError,
    RateLimitError,
    ValidationError,
    error_response,
)

# Tool functions — directly callable
from cn_scraper_mcp.server import (
    harvest_cookies,
    jd_search,
    pdd_product_detail,
    pdd_search,
    taobao_search,
    weibo_user_timeline,
    xiaohongshu_search,
)

# ═══════════════════════════════════════════════════════════════════════
# taobao_search
# ═══════════════════════════════════════════════════════════════════════

class TestTaobaoSearchRaisePath:
    def test_auth_error_returns_session_expired(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = TaobaoAuthError("登录过期")

        with patch("cn_scraper_mcp.engines.TaobaoEngine", return_value=mock_engine):
            result = taobao_search("华为mate70")

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"
        assert result["error"]["retryable"] is True

    def test_api_error_returns_platform_error(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = TaobaoAPIError("API异常")

        with patch("cn_scraper_mcp.engines.TaobaoEngine", return_value=mock_engine):
            result = taobao_search("华为mate70")

        assert result["ok"] is False
        assert result["error"]["code"] == "PLATFORM_ERROR"
        assert result["error"]["retryable"] is True

    def test_file_not_found_returns_legacy_cookie_missing(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = FileNotFoundError("no cookie file")

        with patch("cn_scraper_mcp.engines.TaobaoEngine", return_value=mock_engine):
            result = taobao_search("华为mate70")

        assert result["ok"] is False
        assert result["error"]["code"] == "COOKIE_MISSING"

    def test_unknown_exception_returns_platform_error(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = RuntimeError("内部错误")

        with patch("cn_scraper_mcp.engines.TaobaoEngine", return_value=mock_engine):
            result = taobao_search("华为mate70")

        assert result["ok"] is False
        assert result["error"]["code"] == "PLATFORM_ERROR"

    def test_validation_error_returns_invalid_input(self):
        result = taobao_search("")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════
# jd_search — raise path + dict path
# ═══════════════════════════════════════════════════════════════════════

class TestJDSearchRaisePath:
    def test_login_wall_returns_session_expired(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = JDLoginWallError("login wall")

        with patch("cn_scraper_mcp.engines.JDEngine", return_value=mock_engine):
            result = jd_search("手机")

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"
        assert result["error"]["retryable"] is True

    def test_captcha_returns_captcha_required(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = JDCaptchaError("captcha wall")

        with patch("cn_scraper_mcp.engines.JDEngine", return_value=mock_engine):
            result = jd_search("手机")

        assert result["ok"] is False
        assert result["error"]["code"] == "captcha_required"
        assert result["error"]["retryable"] is True

    def test_chrome_not_found_raises_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = FileNotFoundError("chrome not found")

        with patch("cn_scraper_mcp.engines.JDEngine", return_value=mock_engine):
            result = jd_search("手机")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"


class TestJDSearchDictPath:
    """JD engine returns error dicts (not exceptions) for some failures."""

    def test_chrome_unavailable_dict_returns_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "error": "无法启动京东浏览器",
            "hint": "请确保 Chrome 已安装。Profile 路径: /fake/profile\n首次使用需登录 jd.com。",
        }

        with patch("cn_scraper_mcp.engines.JDEngine", return_value=mock_engine):
            result = jd_search("手机")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"

    def test_cdp_exception_dict_returns_cdp_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "error": "京东搜索异常: Connection refused",
        }

        with patch("cn_scraper_mcp.engines.JDEngine", return_value=mock_engine):
            result = jd_search("手机")

        assert result["ok"] is False
        assert result["error"]["code"] == "cdp_unavailable"


# ═══════════════════════════════════════════════════════════════════════
# pdd_search — raise path
# ═══════════════════════════════════════════════════════════════════════

class TestPDDSearchRaisePath:
    def test_auth_error_returns_session_expired(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = PDDAuthError("token expired")

        with patch("cn_scraper_mcp.engines.PDDEngine", return_value=mock_engine):
            result = pdd_search("test")

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"

    def test_rate_limit_returns_rate_limited(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = PDDRateLimitError("系统繁忙")

        with patch("cn_scraper_mcp.engines.PDDEngine", return_value=mock_engine):
            result = pdd_search("test")

        assert result["ok"] is False
        assert result["error"]["code"] == "RATE_LIMITED"
        assert result["error"]["retryable"] is True

    def test_chrome_not_found_returns_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = FileNotFoundError("chrome not found")

        with patch("cn_scraper_mcp.engines.PDDEngine", return_value=mock_engine):
            result = pdd_search("test")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"


# ═══════════════════════════════════════════════════════════════════════
# pdd_product_detail — raise path
# ═══════════════════════════════════════════════════════════════════════

class TestPDDProductDetail:
    def test_auth_error_returns_session_expired(self):
        mock_engine = MagicMock()
        mock_engine.product_detail.side_effect = PDDAuthError("token expired")

        with patch("cn_scraper_mcp.engines.PDDEngine", return_value=mock_engine):
            result = pdd_product_detail("123456789")

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"

    def test_chrome_not_found_returns_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.product_detail.side_effect = FileNotFoundError("chrome not found")

        with patch("cn_scraper_mcp.engines.PDDEngine", return_value=mock_engine):
            result = pdd_product_detail("123456789")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"

    def test_invalid_input_returns_validation_error(self):
        result = pdd_product_detail("")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════
# xiaohongshu_search — dict path (engine returns state dicts, not exceptions)
# ═══════════════════════════════════════════════════════════════════════

class TestXiaohongshuDictPath:
    def test_login_expired_returns_session_expired(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "login_expired",
            "error_code": "ERR_LOGIN_EXPIRED",
            "keyword": "旅行",
            "items": [],
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "session_expired"

    def test_ip_risk_returns_risk_controlled(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "ip_risk",
            "error_code": "ERR_IP_RISK",
            "keyword": "旅行",
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "risk_controlled"

    def test_captcha_returns_captcha_required(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "captcha",
            "error_code": "ERR_CAPTCHA",
            "keyword": "旅行",
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "captcha_required"

    def test_browser_unavailable_returns_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "error",
            "error_code": "XHS_BROWSER_UNAVAILABLE",
            "hint": "Chrome not found",
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"

    def test_generic_error_returns_platform_error(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "error",
            "error_code": "XHS_SEARCH_EXCEPTION",
            "hint": "Something went wrong",
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "PLATFORM_ERROR"

    def test_ok_state_returns_result_directly(self):
        mock_engine = MagicMock()
        mock_engine.search.return_value = {
            "state": "ok",
            "keyword": "旅行",
            "items": [{"title": "test", "noteId": "abc"}],
        }

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["state"] == "ok"
        assert len(result["items"]) > 0


class TestXiaohongshuRaisePath:
    def test_chrome_not_found_raises_browser_unavailable(self):
        mock_engine = MagicMock()
        mock_engine.search.side_effect = FileNotFoundError("chrome not found")

        with patch("cn_scraper_mcp.engines.XiaohongshuEngine", return_value=mock_engine):
            result = xiaohongshu_search("旅行")

        assert result["ok"] is False
        assert result["error"]["code"] == "browser_unavailable"


# ═══════════════════════════════════════════════════════════════════════
# harvest_cookies — raise path
# ═══════════════════════════════════════════════════════════════════════

class TestHarvestCookies:
    def test_harvest_error_returns_cdp_unavailable(self):
        with patch(
            "cn_scraper_mcp.cookie_harvest.CookieHarvester.harvest",
            side_effect=CookieHarvestError("no page targets"),
        ):
            result = harvest_cookies("taobao")

        assert result["ok"] is False
        assert result["error"]["code"] == "cdp_unavailable"
        assert result["error"]["retryable"] is True

    def test_unsupported_platform_returns_invalid_input(self):
        result = harvest_cookies("unsupported_platform")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════
# weibo_user_timeline — uid validation via error_response
# ═══════════════════════════════════════════════════════════════════════

class TestWeiboUserTimeline:
    def test_invalid_uid_returns_unified_error(self):
        result = weibo_user_timeline("abc")
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ═══════════════════════════════════════════════════════════════════════
# Backward compat: old class names keep old codes
# ═══════════════════════════════════════════════════════════════════════

class TestBackwardCompatOldCodes:
    def test_auth_required_still_returns_auth_required(self):
        e = AuthRequiredError("需要登录")
        d = error_response(e)
        assert d["error"]["code"] == "AUTH_REQUIRED"

    def test_rate_limit_still_returns_rate_limited(self):
        e = RateLimitError("限流")
        d = error_response(e)
        assert d["error"]["code"] == "RATE_LIMITED"

    def test_validation_still_returns_invalid_input(self):
        e = ValidationError("无效参数")
        d = error_response(e)
        assert d["error"]["code"] == "INVALID_INPUT"

    def test_platform_still_returns_platform_error(self):
        e = PlatformError("平台错误")
        d = error_response(e)
        assert d["error"]["code"] == "PLATFORM_ERROR"
