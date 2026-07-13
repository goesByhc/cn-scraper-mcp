"""Scraping engines for Chinese web platforms."""

# Auth / cookie management
from cn_scraper_mcp.auth import CookieFileManager, check_all_cookies

# Re-export unified error model for convenience
from cn_scraper_mcp.errors import (
    AuthRequiredError,
    BrowserError,
    CookieExpiredError,
    CookieMissingError,
    ParseError,
    PlatformError,
    RateLimitError,
    ScraperError,
    ValidationError,
    error_response,
)

from .cdp import (
    CDPClient,
    CDPError,
    close_all_browsers,
    close_browser,
    find_browser,
    find_chrome,
    find_obscura,
    get_browser_lock,
    is_chrome_running,
    launch_chrome,
    launch_obscura,
)
from .jd import JDCaptchaError, JDEmptyError, JDEngine, JDLoginWallError
from .pdd import PDDAuthError, PDDEngine, PDDParseError, PDDRateLimitError, PDDSoldOutError
from .taobao import TaobaoAPIError, TaobaoAuthError, TaobaoEngine
from .xiaohongshu import XiaohongshuEngine
from .zhihu import ZhihuEngine
from .zsxq import ZsxqEngine

__all__ = [
    # E-commerce
    "TaobaoEngine", "TaobaoAuthError", "TaobaoAPIError",
    "JDEngine", "JDLoginWallError", "JDCaptchaError", "JDEmptyError",
    "PDDEngine", "PDDRateLimitError", "PDDAuthError", "PDDParseError", "PDDSoldOutError",
    # Content platforms
    "XiaohongshuEngine", "ZhihuEngine", "ZsxqEngine",
    # CDP utilities — Chrome
    "CDPClient", "find_chrome", "is_chrome_running", "launch_chrome",
    # CDP utilities — Obscura
    "find_obscura", "launch_obscura", "find_browser",
    # CDP lifecycle
    "close_browser", "close_all_browsers", "get_browser_lock",
    "CDPError",
    # Auth / cookies
    "CookieFileManager", "check_all_cookies",
    # Unified error model
    "ScraperError",
    "CookieExpiredError",
    "CookieMissingError",
    "AuthRequiredError",
    "RateLimitError",
    "ParseError",
    "BrowserError",
    "ValidationError",
    "PlatformError",
    "error_response",
]
