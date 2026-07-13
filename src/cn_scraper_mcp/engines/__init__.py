"""Scraping engines for Chinese web platforms."""

from .taobao import TaobaoEngine, TaobaoAuthError, TaobaoAPIError
from .jd import JDEngine
from .cdp import CDPClient, find_chrome, is_chrome_running, launch_chrome
from .xiaohongshu import XiaohongshuEngine
from .zhihu import ZhihuEngine
from .zsxq import ZsxqEngine

__all__ = [
    # E-commerce
    "TaobaoEngine", "TaobaoAuthError", "TaobaoAPIError",
    "JDEngine",
    # Content platforms
    "XiaohongshuEngine", "ZhihuEngine", "ZsxqEngine",
    # CDP utilities
    "CDPClient", "find_chrome", "is_chrome_running", "launch_chrome",
]
