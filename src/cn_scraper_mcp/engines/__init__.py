"""Scraping engines for Chinese web platforms."""

from .taobao import TaobaoEngine, TaobaoAuthError, TaobaoAPIError
from .jd import JDEngine, JDLoginWallError, JDCaptchaError, JDEmptyError
from .cdp import (
    CDPClient, find_chrome, is_chrome_running, launch_chrome,
    find_obscura, launch_obscura, find_browser, close_browser, close_all_browsers,
    CDPError,
)
from .xiaohongshu import XiaohongshuEngine
from .zhihu import ZhihuEngine
from .zsxq import ZsxqEngine

# Auth / cookie management
from cn_scraper_mcp.auth import CookieFileManager, check_all_cookies

# Re-export unified error model for convenience
from cn_scraper_mcp.errors import (
    ScraperError,
    CookieExpiredError,
    CookieMissingError,
    AuthRequiredError,
    RateLimitError,
    ParseError,
    BrowserError,
    ValidationError,
    PlatformError,
    error_response,
)

__all__ = [
    # E-commerce
    "TaobaoEngine", "TaobaoAuthError", "TaobaoAPIError",
    "JDEngine", "JDLoginWallError", "JDCaptchaError", "JDEmptyError",
    # Content platforms
    "XiaohongshuEngine", "ZhihuEngine", "ZsxqEngine",
    # CDP utilities — Chrome
    "CDPClient", "find_chrome", "is_chrome_running", "launch_chrome",
    # CDP utilities — Obscura
    "find_obscura", "launch_obscura", "find_browser",
    # CDP lifecycle
    "close_browser", "close_all_browsers",
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
