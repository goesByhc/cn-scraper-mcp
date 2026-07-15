"""最小可工作的 cn-scraper-mcp 第三方插件示例。

该文件演示了如何实现 PlatformAdapter 协议，以便 cn-scraper-mcp
能够自动发现并加载该插件。

使用方式:
  1. 将本目录复制为新项目
  2. 填写 adapter 的实际业务逻辑
  3. 执行 `pip install -e .` 安装插件
  4. 启动 cn-scraper-mcp — 插件会被自动发现

Required:
  - 实现全部 5 个抽象方法
  - 声明 api_version 和 schema_version
"""

from __future__ import annotations

from cn_scraper_mcp.adapter import PlatformAdapter


class ExampleAdapter(PlatformAdapter):
    """示例适配器 — 一个最小可行的第三方平台插件。"""

    # ── 版本声明（必须）────────────────────────────────────
    # api_version 主版本号需与主机一致（0.x 系列）
    api_version = "0.6"
    # schema_version 需与主机 data schema 版本完全一致
    schema_version = "1.0"

    # ── PlatformAdapter 接口实现 ────────────────────────────

    @property
    def platform_name(self) -> str:
        """平台唯一标识符 — 如 "example"。
        注意: 不能与已有平台 (taobao/jd/pdd/xiaohongshu/zhihu/weibo/douyin/zsxq) 重名。"""
        return "example"

    def search(self, keyword: str, limit: int = 10) -> dict:
        """执行搜索 — 返回统一格式的字典。

        Returns:
            {"keyword": str, "total": int, "items": [...]}
        """
        return {"keyword": keyword, "total": 0, "items": []}

    def validate_session(self) -> dict:
        """验证当前登录态 / 会话是否有效。

        Returns:
            {"valid": bool, "reason": str}
        """
        return {"valid": True, "reason": "always valid (example)"}

    def health_check(self) -> dict:
        """平台健康检查。

        Returns:
            {"platform": str, "status": "healthy|degraded|unavailable",
             "reason": str|None, "latency_ms": float, "adapter_version": str}
        """
        import time

        return {
            "platform": self.platform_name,
            "status": "healthy",
            "reason": None,
            "latency_ms": time.perf_counter() * 0.001,  # dummy
            "adapter_version": "v0.1.0",
        }

    def normalize(self, raw_item: dict):
        """将引擎原始条目转为标准化 SearchItem。

        Args:
            raw_item: 单个引擎返回条目 dict

        Returns:
            SearchItem / ProductItem / ContentItem
        """
        from cn_scraper_mcp.models import SearchItem

        return SearchItem(
            platform=self.platform_name,
            id=raw_item.get("id", ""),
            type="content",
            title=raw_item.get("title", ""),
        )

    # ── 可选覆盖 ────────────────────────────────────────────

    @property
    def capabilities(self) -> dict:
        """平台能力声明。"""
        return {
            "status": "experimental",
            "capabilities": ["search"],
            "requires_login": {"search": False},
        }

    @property
    def timeout_seconds(self) -> int:
        return 30
