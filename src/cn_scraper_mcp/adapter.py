"""统一适配器协议与能力注册表 — ROADMAP §6.1 / §6.2.

PlatformAdapter 为 8 个平台的搜索引擎定义统一接口。
CapabilityRegistry 提供平台发现、能力矩阵生成和工具参数化。

设计目标:
  - 新增平台只需实现 PlatformAdapter 并注册即可接入诊断/聚合/健康检查
  - 注册表可自动生成 README 平台矩阵、健康检查参数、基础测试参数
  - 第三方插件可通过 Python entry points 扩展
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cn_scraper_mcp.models import SearchItem


# ═══════════════════════════════════════════════════════════════
# PlatformAdapter — 统一适配器协议
# ═══════════════════════════════════════════════════════════════


class PlatformAdapter(ABC):
    """所有平台搜索引擎的统一抽象基类。

    每个平台实现必须提供:
      - platform_name: 平台标识符（如 "taobao", "jd"）
      - api_version:   API 兼容版本（如 "0.6"），主版本号相同即兼容
      - schema_version: 数据 schema 版本（如 "1.0"），需精确匹配
      - search:        执行搜索并返回结构化结果
      - validate_session: 检查当前登录态是否有效
      - health_check:  平台健康报告
      - normalize:     将引擎原始条目转为 SearchItem

    可选覆盖:
      - capabilities:       平台能力声明 (status, capabilities, requires_login)
      - timeout_seconds:    默认超时 (默认 30)
      - supports_concurrency: 是否支持并发调用 (默认 False)
    """

    # ── 版本声明 ──────────────────────────────────────────

    api_version: str = "0.6"
    schema_version: str = "1.0"

    # ── 必须实现 ──────────────────────────────────────────

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识符，如 "taobao", "jd", "weibo" 等。"""
        ...

    @abstractmethod
    def search(self, keyword: str, limit: int = 10) -> dict:
        """执行平台搜索。

        Args:
            keyword: 搜索关键词
            limit:   返回条数上限

        Returns:
            {"keyword": str, "total": int, "items": [...]}
            或 {"keyword": str, "count": int, "items": [...]}
        """
        ...

    @abstractmethod
    def validate_session(self) -> dict:
        """验证当前登录态 / 会话是否有效。

        Returns:
            {"valid": bool, "reason": str}
             - valid=True  表示会话可用
             - valid=False 时 reason 说明具体原因 (如 "无 cookie 文件", "token 过期")
        """
        ...

    @abstractmethod
    def health_check(self) -> dict:
        """平台级健康报告 — 符合 ROADMAP §2.2 格式。

        不执行实际搜索，仅检查: 引擎可导入、cookie/凭证可用、
        API 可达 (API 平台) 或 CDP/浏览器可用 (浏览器平台)。

        Returns:
            {
                "platform": str,
                "status": "healthy" | "degraded" | "unavailable",
                "reason": str | None,        # 2.1 统一错误码
                "latency_ms": float,
                "adapter_version": str,
            }
        """
        ...

    @abstractmethod
    def normalize(self, raw_item: dict) -> SearchItem:
        """将引擎返回的原始条目转为标准化 SearchItem / ProductItem / ContentItem。

        Args:
            raw_item: 引擎 search() 返回的 items 列表中的单个 dict

        Returns:
            SearchItem 或其子类 (ProductItem / ContentItem)
        """
        ...

    # ── 可选覆盖 (有合理默认值) ──────────────────────────

    @property
    def capabilities(self) -> dict:
        """平台能力声明。

        Returns:
            {
                "status": "stable" | "experimental" | "limited",
                "capabilities": [str, ...],
                "requires_login": dict[str, bool],
            }
        """
        return {
            "status": "stable",
            "capabilities": ["search"],
            "requires_login": {"search": True},
        }

    @property
    def timeout_seconds(self) -> int:
        """默认操作超时 (秒)。"""
        return 30

    @property
    def supports_concurrency(self) -> bool:
        """该平台是否支持并发调用。"""
        return False


# ═══════════════════════════════════════════════════════════════
# CapabilityRegistry — 能力注册表
# ═══════════════════════════════════════════════════════════════


class CapabilityRegistry:
    """平台适配器注册表。

    集中管理所有 PlatformAdapter，支持按名称查找、列出全部平台、
    生成能力矩阵等。

    Usage:
        registry = CapabilityRegistry()
        registry.register(TaobaoAdapter())
        registry.register(JDAdapter())
        adapter = registry.get("taobao")
        matrix = registry.capability_matrix()
    """

    def __init__(self) -> None:
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        """注册一个平台适配器。

        Args:
            adapter: PlatformAdapter 实例

        Raises:
            ValueError: 如果平台名已存在
        """
        name = adapter.platform_name
        if name in self._adapters:
            raise ValueError(
                f"Platform '{name}' is already registered. "
                f"Use unregister() first if you intend to replace it."
            )
        self._adapters[name] = adapter

    def unregister(self, platform: str) -> None:
        """移除一个已注册的平台。

        Args:
            platform: 平台名

        Raises:
            KeyError: 平台未注册
        """
        if platform not in self._adapters:
            raise KeyError(f"Platform '{platform}' is not registered.")
        del self._adapters[platform]

    def get(self, platform: str) -> PlatformAdapter:
        """按平台名获取适配器。

        Args:
            platform: 平台标识符

        Returns:
            PlatformAdapter 实例

        Raises:
            KeyError: 平台未注册
        """
        if platform not in self._adapters:
            raise KeyError(
                f"Platform '{platform}' is not registered. "
                f"Available: {sorted(self._adapters.keys())}"
            )
        return self._adapters[platform]

    def list_all(self) -> list[PlatformAdapter]:
        """返回所有已注册的适配器列表。"""
        return list(self._adapters.values())

    def platforms(self) -> list[str]:
        """返回所有已注册平台名的排序列表。"""
        return sorted(self._adapters.keys())

    def capability_matrix(self) -> dict:
        """生成所有平台的能力矩阵。

        Returns:
            {
                "platforms": {
                    "<name>": {
                        "status": str,
                        "capabilities": [str, ...],
                        "requires_login": dict[str, bool],
                        "timeout_seconds": int,
                        "supports_concurrency": bool,
                    },
                    ...
                }
            }
        """
        matrix: dict = {}
        for name, adapter in sorted(self._adapters.items()):
            caps = adapter.capabilities
            matrix[name] = {
                "status": caps.get("status", "unknown"),
                "capabilities": caps.get("capabilities", []),
                "requires_login": caps.get("requires_login", {}),
                "timeout_seconds": adapter.timeout_seconds,
                "supports_concurrency": adapter.supports_concurrency,
            }
        return {"platforms": matrix}


# ═══════════════════════════════════════════════════════════════
# 自动生成工具 — README 矩阵 / 健康检查 / 测试参数
# ═══════════════════════════════════════════════════════════════


def generate_readme_matrix(registry: CapabilityRegistry) -> str:
    """根据注册表生成 README 平台矩阵的 Markdown 表格。

    Returns:
        Markdown 格式的平台状态表
    """
    lines = [
        "| 平台 | 状态 | 支持能力 | 需要登录 | 超时 (秒) | 并发安全 |",
        "|------|------|----------|----------|-----------|----------|",
    ]

    _status_label = {
        "stable": "✅ 稳定",
        "experimental": "⚠️ 实验性",
        "limited": "🔶 受限",
    }

    for name in registry.platforms():
        adapter = registry.get(name)
        caps = adapter.capabilities
        status_label = _status_label.get(caps.get("status", ""), caps.get("status", "未知"))
        cap_list = ", ".join(caps.get("capabilities", []))
        req_login = caps.get("requires_login", {})
        login_str = ", ".join(
            f"{k}: {'是' if v else '否'}" for k, v in sorted(req_login.items())
        )
        concurrency = "✅" if adapter.supports_concurrency else "⚠️ 串行"

        lines.append(
            f"| {name} | {status_label} | {cap_list} | {login_str} | "
            f"{adapter.timeout_seconds} | {concurrency} |"
        )

    return "\n".join(lines)


def generate_health_check_params(registry: CapabilityRegistry) -> dict:
    """根据注册表生成健康检查脚本的参数化配置。

    可用于 scripts/platform_health.py 的动态平台发现。

    Returns:
        {
            "platforms": {
                "<name>": {
                    "engine_class": str,
                    "engine_module": str,
                    "type": "api" | "browser",
                    "cookie_platform": str,
                },
                ...
            }
        }
    """
    params: dict = {}
    for name in registry.platforms():
        adapter = registry.get(name)
        params[name] = {
            "cookie_platform": name,
            "timeout_seconds": adapter.timeout_seconds,
            "supports_concurrency": adapter.supports_concurrency,
            "capabilities": adapter.capabilities.get("capabilities", []),
        }
    return {"platforms": params}


def generate_test_params(registry: CapabilityRegistry) -> dict:
    """根据注册表生成基础测试的参数化配置。

    可用于 pytest.mark.parametrize 动态生成 per-platform 测试用例。

    Returns:
        {
            "platforms": [str, ...],
            "test_configs": {
                "<name>": {
                    "timeout_seconds": int,
                    "expected_methods": [str, ...],
                },
                ...
            }
        }
    """
    platforms = registry.platforms()
    configs: dict = {}
    for name in platforms:
        adapter = registry.get(name)
        configs[name] = {
            "timeout_seconds": adapter.timeout_seconds,
            "expected_methods": adapter.capabilities.get("capabilities", []),
        }
    return {"platforms": platforms, "test_configs": configs}


# ═══════════════════════════════════════════════════════════════
# TaobaoAdapter — 第一个适配器迁移示例
# ═══════════════════════════════════════════════════════════════


class TaobaoAdapter(PlatformAdapter):
    """淘宝/天猫适配器 — 封装 TaobaoEngine，演示适配器模式。

    将现有 TaobaoEngine 包装为 PlatformAdapter 协议，不影响原有调用路径。
    server.py 中的 taobao_search 工具函数可逐步迁移到通过适配器调用。
    """

    def __init__(self, cookies_path: str | None = None):
        """初始化淘宝适配器。

        Args:
            cookies_path: Cookie 文件路径。默认使用环境变量或标准路径。
        """
        from cn_scraper_mcp.engines.taobao import TaobaoEngine

        self._engine = TaobaoEngine(cookies_path=cookies_path)

    @property
    def platform_name(self) -> str:
        return "taobao"

    def search(self, keyword: str, limit: int = 10) -> dict:
        """搜索淘宝/天猫商品。委托给 TaobaoEngine.search()。"""
        return self._engine.search(keyword, limit=limit)

    def validate_session(self) -> dict:
        """验证淘宝登录态。

        检查 cookie 文件是否存在、是否包含 _m_h5_tk token。
        """
        import os
        from pathlib import Path

        cookies_path = os.environ.get("TAOBAO_COOKIES_FILE") or str(
            Path.home() / ".cn-scraper-cookies" / "taobao.json"
        )

        if not os.path.exists(cookies_path):
            return {
                "valid": False,
                "reason": f"Cookie 文件不存在: {cookies_path}",
            }

        try:
            import json

            cookies = json.load(open(cookies_path, encoding="utf-8"))
        except (ValueError, OSError) as e:
            return {
                "valid": False,
                "reason": f"Cookie 文件无法解析: {e}",
            }

        if "_m_h5_tk" not in cookies:
            return {
                "valid": False,
                "reason": "缺少必要字段 _m_h5_tk",
            }

        return {"valid": True, "reason": "Cookie 文件存在且包含必要字段"}

    def health_check(self) -> dict:
        """淘宝平台健康检查。

        检查: 引擎可导入、适配器版本、基础连通性。
        """
        import time

        from cn_scraper_mcp import __version__

        t0 = time.perf_counter()

        try:
            # 验证引擎可导入
            import importlib

            importlib.import_module("cn_scraper_mcp.engines.taobao")

            has_token = bool(self._engine.cookies.get("_m_h5_tk"))

            return {
                "platform": "taobao",
                "status": "healthy" if has_token else "degraded",
                "reason": None if has_token else "session_expired",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                "adapter_version": f"v{__version__}",
            }
        except ImportError:
            return {
                "platform": "taobao",
                "status": "unavailable",
                "reason": "api_changed",
                "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
                "adapter_version": f"v{__version__}",
            }

    def normalize(self, raw_item: dict) -> SearchItem:
        """将淘宝 MTOP 原始条目转为 ProductItem。"""
        from cn_scraper_mcp.models import normalize_taobao

        return normalize_taobao(raw_item)

    @property
    def capabilities(self) -> dict:
        return {
            "status": "stable",
            "capabilities": ["search", "item_detail"],
            "requires_login": {"search": True, "item_detail": True},
        }

    @property
    def timeout_seconds(self) -> int:
        return 15

    @property
    def supports_concurrency(self) -> bool:
        return True
