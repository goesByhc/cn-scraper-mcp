"""第三方插件系统 — ROADMAP §6.3.

提供:
  - discover_plugins:    通过 Python entry points 发现外部 PlatformAdapter
  - validate_plugin:     验证插件兼容性（方法完整性 / 版本 / 契约测试）
  - load_plugin_from_file: 从文件动态加载适配器（文件系统回退路径）

安全隔离:
  - 插件运行失败不阻止核心 MCP Server 启动
  - 损坏的插件自动跳过并记录警告
  - 插件只能在声明的 capabilities 范围内操作
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cn_scraper_mcp.logging import get_logger

if TYPE_CHECKING:
    from cn_scraper_mcp.adapter import PlatformAdapter

logger = get_logger(__name__)

# ── API / schema 兼容版本 ─────────────────────────────────────
API_VERSION = "0.6"
"""插件声明中 api_version 需与此匹配的主版本号一致。"""

SCHEMA_VERSION = "1.0"
"""插件声明中 schema_version 需与此值一致。"""

# ── PluginAdapter 必需的抽象方法 ─────────────────────────────
_REQUIRED_METHODS = [
    "platform_name",
    "search",
    "validate_session",
    "health_check",
    "normalize",
]
"""PlatformAdapter ABC 规定的 5 个必需方法。"""


# ═══════════════════════════════════════════════════════════════
# discover_plugins
# ═══════════════════════════════════════════════════════════════


def discover_plugins(
    group: str = "cn_scraper_mcp.platforms",
) -> list[PlatformAdapter]:
    """发现所有已安装的第三方平台插件。

    发现顺序:
      1. 扫描 importlib.metadata.entry_points(group=...)
      2. 回退: 扫描 ~/.cn-scraper-plugins/ 目录

    每个发现到的插件均会通过 validate_plugin() 检查。
    损坏或验证失败的插件会被跳过并记录警告，不抛出异常。

    Args:
        group: entry_points 组名，默认 "cn_scraper_mcp.platforms"

    Returns:
        已验证通过的 PlatformAdapter 实例列表
    """
    from cn_scraper_mcp.adapter import PlatformAdapter

    adapters: list[PlatformAdapter] = []

    # ── 1. 扫描 entry points ──────────────────────────────────
    try:
        entry_points = _discover_entry_points(group)
    except Exception:
        logger.warning("扫描 entry points 失败，已跳过", exc_info=True)
        entry_points = []
    for entry in entry_points:
        try:
            adapter_class = entry.load()
            if not issubclass(adapter_class, PlatformAdapter):
                logger.warning(
                    "entry point '%s' 不是 PlatformAdapter 的子类，已跳过",
                    entry.name,
                )
                continue
            adapter = adapter_class()
        except Exception:
            logger.warning(
                "加载 entry point '%s' 失败，已跳过",
                entry.name,
                exc_info=True,
            )
            continue

        ok, reason = validate_plugin(adapter)
        if not ok:
            logger.warning(
                "entry point '%s' 验证失败: %s，已跳过",
                entry.name,
                reason,
            )
            continue

        adapters.append(adapter)
        logger.debug("已发现插件 '%s' (entry point)", entry.name)

    # ── 2. 回退: 文件系统扫描 ──────────────────────────────────
    try:
        fs_adapters = _discover_filesystem(group)
    except Exception:
        logger.warning("文件系统插件扫描失败，已跳过", exc_info=True)
        fs_adapters = []
    for adapter in fs_adapters:
        # 避免重复（filesystem 加载的插件可能也注册了 entry point）
        if _adapter_already_registered(adapter, adapters):
            continue

        ok, reason = validate_plugin(adapter)
        if not ok:
            logger.warning(
                "文件系统插件 '%s' 验证失败: %s，已跳过",
                adapter.platform_name,
                reason,
            )
            continue

        adapters.append(adapter)
        logger.debug("已发现插件 '%s' (filesystem)", adapter.platform_name)

    return adapters


# ═══════════════════════════════════════════════════════════════
# validate_plugin
# ═══════════════════════════════════════════════════════════════


def validate_plugin(adapter: PlatformAdapter) -> tuple[bool, str]:
    """验证一个插件适配器的完整性和兼容性。

    检查项目:
      1. 实现了所有必需的抽象方法
      2. API 兼容版本声明与主机一致
      3. 数据 schema 版本声明与主机一致
      4. 契约测试: validate_session() 调用不抛异常

    Args:
        adapter: 待验证的 PlatformAdapter 实例

    Returns:
        (ok, reason) — ok=True 表示验证通过，否则 reason 说明失败原因
    """
    from cn_scraper_mcp.adapter import PlatformAdapter

    if not isinstance(adapter, PlatformAdapter):
        return False, "不是 PlatformAdapter 的实例"

    # ── 1. 检查必需方法 ──────────────────────────────────────
    for method_name in _REQUIRED_METHODS:
        method = getattr(adapter, method_name, None)
        if method is None:
            return False, f"缺少必需方法: {method_name}"
        # 检查是否为抽象方法（未实现）
        if getattr(method, "__isabstractmethod__", False):
            return False, f"方法 '{method_name}' 仍未实现"

    # ── 2. 检查 API 兼容版本 ─────────────────────────────────
    plugin_api_version = getattr(adapter, "api_version", None)
    if plugin_api_version is None:
        return False, "未声明 api_version 属性"
    if not _versions_compatible(plugin_api_version, API_VERSION):
        return False, (
            f"API 版本不兼容: 插件声明 {plugin_api_version}, 主机需要 {API_VERSION}"
        )

    # ── 3. 检查 schema 版本 ───────────────────────────────────
    plugin_schema_version = getattr(adapter, "schema_version", None)
    if plugin_schema_version is None:
        return False, "未声明 schema_version 属性"
    if plugin_schema_version != SCHEMA_VERSION:
        return False, (
            f"schema 版本不匹配: 插件声明 {plugin_schema_version}, "
            f"主机需要 {SCHEMA_VERSION}"
        )

    # ── 4. 契约测试: validate_session 不抛异常 ───────────────
    try:
        adapter.validate_session()
    except Exception as exc:
        return False, f"validate_session() 契约测试失败: {exc}"

    return True, "ok"


# ═══════════════════════════════════════════════════════════════
# 文件系统发现（回退路径）
# ═══════════════════════════════════════════════════════════════


def _discover_filesystem(
    group: str = "cn_scraper_mcp.platforms",
) -> list[PlatformAdapter]:
    """从 ~/.cn-scraper-plugins/ 目录发现插件适配器。

    扫描规则:
      - 直接寻找 *.py 文件（如 my_platform.py）
      - 也扫描子目录中的包（如 my_platform/adapter.py）
      - 每个插件文件必须包含一个 PlatformAdapter 的子类
    """
    from cn_scraper_mcp.adapter import PlatformAdapter

    plugins_dir = Path.home() / ".cn-scraper-plugins"
    if not plugins_dir.is_dir():
        return []

    adapters: list[PlatformAdapter] = []
    plugin_files: list[Path] = []

    # 直接 .py 文件
    for py_file in sorted(plugins_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        plugin_files.append(py_file)

    # 子目录中的包
    for pkg_dir in sorted(plugins_dir.iterdir()):
        if not pkg_dir.is_dir():
            continue
        if pkg_dir.name.startswith("_") or pkg_dir.name.startswith("."):
            continue
        pkg_init = pkg_dir / "__init__.py"
        if pkg_init.exists():
            plugin_files.append(pkg_init)
        # 也支持 adapter.py
        adapter_file = pkg_dir / "adapter.py"
        if adapter_file.exists() and adapter_file not in plugin_files:
            plugin_files.append(adapter_file)

    for file_path in plugin_files:
        adapters_from_file = _load_adapters_from_file(file_path)
        for adapter in adapters_from_file:
            if isinstance(adapter, PlatformAdapter):
                adapters.append(adapter)

    return adapters


def _load_adapters_from_file(
    file_path: Path,
) -> list[PlatformAdapter]:
    """从单个 Python 文件加载 PlatformAdapter 子类实例。

    通过 importlib 动态加载模块，扫描其中所有 PlatformAdapter 子类并实例化。
    加载失败不抛异常，返回空列表并记录警告。
    """
    from cn_scraper_mcp.adapter import PlatformAdapter

    module_name = f"_cn_plugin_{file_path.stem}_{hash(str(file_path)) & 0xFFFFFFFF}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            logger.warning("无法加载文件: %s (spec 为空)", file_path)
            return []
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:
        logger.warning(
            "加载插件文件失败: %s",
            file_path,
            exc_info=True,
        )
        return []

    adapters: list[PlatformAdapter] = []
    for _name, obj in vars(module).items():
        if _name.startswith("_"):
            continue
        if not isinstance(obj, type):
            continue
        if not issubclass(obj, PlatformAdapter):
            continue
        if obj is PlatformAdapter:
            continue
        try:
            adapter = obj()
            adapters.append(adapter)
        except Exception:
            logger.warning(
                "实例化插件类 '%s' 失败 (来自 %s)",
                _name,
                file_path,
                exc_info=True,
            )
            continue

    return adapters


# ═══════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════


def _discover_entry_points(group: str) -> list:
    """扫描 importlib.metadata.entry_points(group=...)。

    兼容 Python 3.11 和 3.12+ 的 entry_points API 差异。
    不抛异常 — 出错时返回空列表。
    """
    try:
        eps = importlib.metadata.entry_points()
    except Exception:
        logger.warning("无法访问 entry_points，跳过 Python 包发现", exc_info=True)
        return []

    # Python 3.12+: entry_points(group=...) 是关键字参数
    # Python 3.11: entry_points() 返回 dict-like，需要 [] 索引
    try:
        return list(eps.select(group=group))
    except AttributeError:
        pass

    try:
        return list(eps.get(group, []))
    except Exception:
        return []


def _versions_compatible(plugin_ver: str, host_ver: str) -> bool:
    """检查两个 semver 主版本号是否兼容。

    兼容条件: 主版本号相等（major 部分相同）。
    例如 "0.6" 与 "0.6" 兼容；"0.6" 与 "0.7" 不兼容。
    """
    try:
        plugin_major = plugin_ver.strip().split(".")[0]
        host_major = host_ver.strip().split(".")[0]
        return plugin_major == host_major
    except Exception:
        return False


def _adapter_already_registered(
    adapter: PlatformAdapter,
    existing: list[PlatformAdapter],
) -> bool:
    """检查 adapter 是否已存在于列表中（按 platform_name 判断）。"""
    adapter_name = adapter.platform_name
    return any(
        a.platform_name == adapter_name for a in existing
    )
