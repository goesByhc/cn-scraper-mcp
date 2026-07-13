"""Douyin (抖音) search engine — ⚠️ EXPERIMENTAL / SKELETON ONLY.

Douyin is the most aggressively defended Chinese platform:

    - All search/aweme APIs require **signed requests** (X-Gorgon, X-Khronos, X-Argus headers).
    - Signature algorithms use obfuscated native code and change frequently.
    - Device registration + emulator fingerprinting is required.
    - No known guest-friendly public endpoint exists for programmatic search.

ALTERNATIVES for accessing Douyin content:

    - Use douyin.com in a real mobile browser (difficult to automate at scale).
    - Third-party data providers (e.g. 飞瓜数据, 蝉妈妈) — paid services.
    - Use the official Douyin Open API (需要企业资质审核).
    - Video/livestream monitoring via TikTok's less-gated research API (different platform).

This engine exists as a **skeleton with an honest error message**.
It does NOT attempt to reverse-engineer signatures — that approach
would break quickly and mislead users about reliability.

If a guest-friendly endpoint is discovered in the future, implement it here.
"""

import json
import os
from pathlib import Path

from cn_scraper_mcp.http import HttpClient

# ═══════════════════════════════════════════════════════════════════════════
# Truth-in-advertising: why Douyin scraping is essentially impossible
# ═══════════════════════════════════════════════════════════════════════════

_DOUYIN_DISCLAIMER = (
    "抖音 (Douyin) 抓取目前不可行。\n\n"
    "原因:\n"
    "  1. 所有搜索/视频 API 需要签名请求 (X-Gorgon / X-Khronos / X-Argus)\n"
    "  2. 签名算法使用混淆后的 native 代码，且频繁更新\n"
    "  3. 需要设备注册 + 模拟器指纹\n"
    "  4. 没有已知的游客可用的公开搜索端点\n\n"
    "替代方案:\n"
    "  - 在真实手机浏览器中使用 douyin.com (难以规模化)\n"
    "  - 第三方数据服务: 飞瓜数据、蝉妈妈等 (付费)\n"
    "  - 抖音开放平台 API (需要企业资质审核)\n"
    "  - TikTok Research API (不同平台，限制较少但数据不同)"
)


class DouyinEngine:
    """Douyin (抖音) search engine — EXPERIMENTAL SKELETON.

    **This engine does NOT perform actual scraping.** Douyin requires
    cryptographically signed API requests that are infeasible to generate
    without reverse-engineering obfuscated native binaries.

    This class exists to:
    - Provide an honest, actionable error message to users
    - Serve as a placeholder for when a guest-friendly endpoint emerges
    - Document the current state of Douyin anti-bot defenses

    Usage:
        engine = DouyinEngine()
        result = engine.search("美食")  # Returns error with alternatives
    """

    def __init__(self, cookies_path: str | None = None):
        if cookies_path is None:
            cookies_path = os.environ.get(
                "DOUYIN_COOKIES_FILE"
            ) or str(Path.home() / ".cn-scraper-cookies" / "douyin.json")
        self.cookies_path = cookies_path
        self.cookies = {}
        if os.path.exists(cookies_path):
            try:
                self.cookies = json.load(open(cookies_path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.cookies = {}

    def search(self, keyword: str, limit: int = 10) -> dict:
        """Search Douyin — **NOT IMPLEMENTED**.

        Returns an honest error message explaining why Douyin scraping
        is currently infeasible and what alternatives exist.

        Args:
            keyword: Search query (accepted but ignored)
            limit: Max results (accepted but ignored)

        Returns:
            {
                "error": str,
                "hint": str,
                "alternatives": [...],
                "keyword": str,
                "status": "UNSUPPORTED",
            }
        """
        return {
            "keyword": keyword,
            "error": _DOUYIN_DISCLAIMER,
            "status": "UNSUPPORTED",
            "alternatives": [
                {"name": "飞瓜数据 (Feigua)", "url": "https://dy.feigua.cn/", "type": "paid"},
                {"name": "蝉妈妈 (Chanmama)", "url": "https://www.chanmama.com/", "type": "paid"},
                {"name": "抖音开放平台", "url": "https://open.douyin.com/", "type": "official_api"},
                {"name": "TikTok Research API", "url": "https://developers.tiktok.com/products/research-api/", "type": "different_platform"},
            ],
            "count": 0,
            "items": [],
        }
