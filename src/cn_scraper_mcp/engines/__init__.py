"""Platform engines with lazy compatibility exports.

MCP tools import platform modules directly. The lazy names below preserve the
pre-1.0 Python API without importing every platform when one engine is used.
"""

from importlib import import_module

_EXPORTS: dict[str, tuple[str, str]] = {
    # Engines and platform-specific exceptions
    "TaobaoEngine": (".taobao", "TaobaoEngine"),
    "TaobaoAuthError": (".taobao", "TaobaoAuthError"),
    "TaobaoAPIError": (".taobao", "TaobaoAPIError"),
    "JDEngine": (".jd", "JDEngine"),
    "JDLoginWallError": (".jd", "JDLoginWallError"),
    "JDCaptchaError": (".jd", "JDCaptchaError"),
    "JDEmptyError": (".jd", "JDEmptyError"),
    "PDDEngine": (".pdd", "PDDEngine"),
    "PDDRateLimitError": (".pdd", "PDDRateLimitError"),
    "PDDAuthError": (".pdd", "PDDAuthError"),
    "PDDParseError": (".pdd", "PDDParseError"),
    "PDDSoldOutError": (".pdd", "PDDSoldOutError"),
    "WeiboEngine": (".weibo", "WeiboEngine"),
    "DouyinEngine": (".douyin", "DouyinEngine"),
    "BilibiliEngine": (".bilibili", "BilibiliEngine"),
    "XiaohongshuEngine": (".xiaohongshu", "XiaohongshuEngine"),
    "ZhihuEngine": (".zhihu", "ZhihuEngine"),
    "ZsxqEngine": (".zsxq", "ZsxqEngine"),
    "DoubanEngine": (".douban", "DoubanEngine"),
    "DianpingEngine": (".dianping", "DianpingEngine"),
    # CDP utilities
    "CDPClient": (".cdp", "CDPClient"),
    "CDPError": (".cdp", "CDPError"),
    "find_chrome": (".cdp", "find_chrome"),
    "is_chrome_running": (".cdp", "is_chrome_running"),
    "launch_chrome": (".cdp", "launch_chrome"),
    "find_obscura": (".cdp", "find_obscura"),
    "launch_obscura": (".cdp", "launch_obscura"),
    "find_browser": (".cdp", "find_browser"),
    "close_browser": (".cdp", "close_browser"),
    "close_all_browsers": (".cdp", "close_all_browsers"),
    "get_browser_lock": (".cdp", "get_browser_lock"),
    # Auth compatibility exports
    "CookieFileManager": ("cn_scraper_mcp.auth", "CookieFileManager"),
    "check_all_cookies": ("cn_scraper_mcp.auth", "check_all_cookies"),
}

for _name in (
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
):
    _EXPORTS[_name] = ("cn_scraper_mcp.errors", _name)

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
