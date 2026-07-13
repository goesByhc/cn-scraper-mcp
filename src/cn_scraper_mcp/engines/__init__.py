"""Scraping engines for Chinese web platforms."""

from .taobao import TaobaoEngine, TaobaoAuthError, TaobaoAPIError
from .jd import JDEngine
from .cdp import (
    CDPClient, find_chrome, is_chrome_running, launch_chrome,
    find_obscura, launch_obscura, find_browser,
    CDPError,
)
from .xiaohongshu import XiaohongshuEngine
from .zhihu import ZhihuEngine
from .zsxq import ZsxqEngine

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
    "JDEngine",
    # Content platforms
    "XiaohongshuEngine", "ZhihuEngine", "ZsxqEngine",
    # CDP utilities — Chrome
    "CDPClient", "find_chrome", "is_chrome_running", "launch_chrome",
    # CDP utilities — Obscura
    "find_obscura", "launch_obscura", "find_browser",
    "CDPError",
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
