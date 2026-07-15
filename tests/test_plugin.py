"""Unit tests for plugin module — discover_plugins, validate_plugin, filesystem fallback.

ALL mocks — no real entry_points, filesystem, or network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.adapter import PlatformAdapter
from cn_scraper_mcp.models import SearchItem
from cn_scraper_mcp.plugin import (
    API_VERSION,
    SCHEMA_VERSION,
    _discover_filesystem,
    _load_adapters_from_file,
    _versions_compatible,
    discover_plugins,
    validate_plugin,
)

# ── 最小可用的第三方适配器 ─────────────────────────────────────


class _ValidPluginAdapter(PlatformAdapter):
    """验证通过的插件适配器 — 实现全部必需方法并声明版本。"""

    api_version = API_VERSION
    schema_version = SCHEMA_VERSION

    @property
    def platform_name(self) -> str:
        return "example_plugin"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {"keyword": keyword, "total": 0, "items": []}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {
            "platform": "example_plugin",
            "status": "healthy",
            "reason": None,
            "latency_ms": 1.0,
            "adapter_version": "v0.1.0",
        }

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(
            platform="example_plugin",
            id=raw_item.get("id", ""),
            type="content",
            title=raw_item.get("title", ""),
        )

    @property
    def capabilities(self) -> dict:
        return {
            "status": "experimental",
            "capabilities": ["search"],
            "requires_login": {"search": False},
        }


# ── 各种有问题的适配器 ─────────────────────────────────────────


class _MissingApiVersion(PlatformAdapter):
    """缺少 api_version 声明。"""

    api_version = None
    schema_version = SCHEMA_VERSION

    @property
    def platform_name(self) -> str:
        return "missing_api_ver"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {"keyword": keyword, "total": 0, "items": []}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {}

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(platform="x", id="", type="content", title="")


class _MissingSchemaVersion(PlatformAdapter):
    """缺少 schema_version 声明。"""

    api_version = API_VERSION
    schema_version = None

    @property
    def platform_name(self) -> str:
        return "missing_schema_ver"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {"keyword": keyword, "total": 0, "items": []}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {}

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(platform="x", id="", type="content", title="")


class _IncompatibleApiVersion(PlatformAdapter):
    """API 版本不兼容。"""

    api_version = "99.0"
    schema_version = SCHEMA_VERSION

    @property
    def platform_name(self) -> str:
        return "incompat_api_ver"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {}

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(platform="x", id="", type="content", title="")


class _IncompatibleSchemaVersion(PlatformAdapter):
    """Schema 版本不匹配。"""

    api_version = API_VERSION
    schema_version = "99.0"

    @property
    def platform_name(self) -> str:
        return "incompat_schema"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {}

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(platform="x", id="", type="content", title="")


class _CrashingValidateSession(PlatformAdapter):
    """validate_session 抛出异常。"""

    api_version = API_VERSION
    schema_version = SCHEMA_VERSION

    @property
    def platform_name(self) -> str:
        return "crashing"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {}

    def validate_session(self) -> dict:
        raise RuntimeError("boom")

    def health_check(self) -> dict:
        return {}

    def normalize(self, raw_item: dict) -> SearchItem:
        return SearchItem(platform="x", id="", type="content", title="")


class _IncompleteAdapter(PlatformAdapter):
    """部分抽象方法未实现的适配器。"""

    @property
    def platform_name(self) -> str:
        return "incomplete"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {}

    # 缺少: validate_session, health_check, normalize


# ── validate_plugin 测试 ───────────────────────────────────────


class TestValidatePlugin:
    """测试 validate_plugin 对各种适配器的验证行为。"""

    def test_valid_adapter_passes(self):
        """完全实现的适配器应通过验证。"""
        ok, reason = validate_plugin(_ValidPluginAdapter())
        assert ok is True
        assert reason == "ok"

    def test_non_adapter_rejected(self):
        """非 PlatformAdapter 实例被拒绝。"""
        ok, reason = validate_plugin("not an adapter")  # type: ignore[arg-type]
        assert ok is False
        assert "不是" in reason

    def test_missing_api_version_rejected(self):
        """缺少 api_version 声明被拒绝。"""
        ok, reason = validate_plugin(_MissingApiVersion())
        assert ok is False
        assert "api_version" in reason

    def test_missing_schema_version_rejected(self):
        """缺少 schema_version 声明被拒绝。"""
        ok, reason = validate_plugin(_MissingSchemaVersion())
        assert ok is False
        assert "schema_version" in reason

    def test_incompatible_api_version_rejected(self):
        """主版本号不匹配被拒绝。"""
        ok, reason = validate_plugin(_IncompatibleApiVersion())
        assert ok is False
        assert "不兼容" in reason or "API" in reason

    def test_incompatible_schema_version_rejected(self):
        """schema 版本不匹配被拒绝。"""
        ok, reason = validate_plugin(_IncompatibleSchemaVersion())
        assert ok is False
        assert "schema" in reason.lower()

    def test_crashing_validate_session_rejected(self):
        """validate_session 抛异常被拒绝。"""
        ok, reason = validate_plugin(_CrashingValidateSession())
        assert ok is False
        assert "契约测试" in reason

    def test_incomplete_adapter_cannot_instantiate(self):
        """缺少抽象方法的适配器无法被实例化。"""
        with pytest.raises(TypeError):
            _IncompleteAdapter()  # type: ignore[abstract]


# ── discover_plugins 测试 ──────────────────────────────────────


class TestDiscoverPlugins:
    """测试 discover_plugins 的入口点扫描和文件系统回退。"""

    def test_empty_environment_returns_empty(self):
        """没有插件时返回空列表。"""
        with patch("cn_scraper_mcp.plugin._discover_entry_points", return_value=[]):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert result == []

    def test_valid_entry_point_returns_adapter(self):
        """有效 entry point 插件被成功发现。"""
        mock_entry = MagicMock()
        mock_entry.name = "example"
        mock_entry.load.return_value = _ValidPluginAdapter

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert len(result) == 1
                assert result[0].platform_name == "example_plugin"

    def test_entry_point_load_failure_is_skipped(self):
        """entry point 加载失败不中断发现流程。"""
        mock_entry = MagicMock()
        mock_entry.name = "bad"
        mock_entry.load.side_effect = ImportError("module not found")

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert result == []

    def test_entry_point_validation_failure_is_skipped(self):
        """验证失败的 entry point 被跳过。"""
        mock_entry = MagicMock()
        mock_entry.name = "incompat"
        mock_entry.load.return_value = _IncompatibleApiVersion

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert result == []

    def test_non_platform_adapter_entry_point_skipped(self):
        """非 PlatformAdapter 子类的 entry point 被跳过。"""
        mock_entry = MagicMock()
        mock_entry.name = "not_adapter"
        mock_entry.load.return_value = str  # str is not PlatformAdapter

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert result == []

    def test_filesystem_fallback_returns_adapters(self):
        """文件系统回退发现插件。"""
        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[]
        ):
            fs_adapter = _ValidPluginAdapter()
            with patch(
                "cn_scraper_mcp.plugin._discover_filesystem",
                return_value=[fs_adapter],
            ):
                result = discover_plugins()
                assert len(result) == 1
                assert result[0].platform_name == "example_plugin"

    def test_duplicate_adapters_deduplicated(self):
        """文件系统插件和 entry point 重复时去重。"""
        mock_entry = MagicMock()
        mock_entry.name = "example"
        mock_entry.load.return_value = _ValidPluginAdapter

        fs_adapter = _ValidPluginAdapter()

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch(
                "cn_scraper_mcp.plugin._discover_filesystem",
                return_value=[fs_adapter],
            ):
                result = discover_plugins()
                assert len(result) == 1

    def test_mixed_valid_and_invalid_entry_points(self):
        """有效和无效插件混合时只返回有效者。"""
        valid_entry = MagicMock()
        valid_entry.name = "valid"
        valid_entry.load.return_value = _ValidPluginAdapter

        invalid_entry = MagicMock()
        invalid_entry.name = "invalid"
        invalid_entry.load.side_effect = ImportError("fail")

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points",
            return_value=[valid_entry, invalid_entry],
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert len(result) == 1
                assert result[0].platform_name == "example_plugin"

    def test_discover_never_raises(self):
        """discover_plugins 任何情况下不向外抛异常。"""
        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points",
            side_effect=Exception("catastrophic"),
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins()
                assert result == []


# ── _versions_compatible 测试 ──────────────────────────────────


class TestVersionsCompatible:
    """测试版本兼容性判断。"""

    def test_same_version_compatible(self):
        assert _versions_compatible("0.6", "0.6") is True

    def test_same_major_compatible(self):
        assert _versions_compatible("0.6.1", "0.6") is True

    def test_different_major_incompatible(self):
        assert _versions_compatible("1.0", "0.6") is False

    def test_invalid_plugin_version(self):
        assert _versions_compatible("abc", "0.6") is False

    def test_minor_diff_still_compatible(self):
        """minor/patch 版本不同但 major 一致时兼容。"""
        assert _versions_compatible("0.7", "0.6") is True
        assert _versions_compatible("0.10.5", "0.6") is True


# ── _discover_filesystem 文件系统测试 ───────────────────────────


class TestDiscoverFilesystem:
    """测试从 ~/.cn-scraper-plugins/ 目录发现插件。"""

    def test_dir_not_exists_returns_empty(self):
        """目录不存在返回空。"""
        with patch.object(Path, "home", return_value=Path("/nonexistent_xyz")):
            with patch.object(Path, "is_dir", return_value=False):
                result = _discover_filesystem()
                assert result == []

    def test_empty_dir_returns_empty(self, tmp_path: Path):
        """空目录返回空。"""
        plugins_dir = tmp_path / ".cn-scraper-plugins"
        plugins_dir.mkdir()
        with patch.object(Path, "home", return_value=tmp_path):
            result = _discover_filesystem()
            assert result == []

    def test_py_file_with_adapter_is_loaded(self, tmp_path: Path):
        """目录中的 .py 文件包含适配器时被加载。"""
        plugins_dir = tmp_path / ".cn-scraper-plugins"
        plugins_dir.mkdir()

        plugin_code = """from cn_scraper_mcp.adapter import PlatformAdapter
from cn_scraper_mcp.models import SearchItem
class MyAdapter(PlatformAdapter):
    api_version = "0.6"
    schema_version = "1.0"
    @property
    def platform_name(self): return "myplug"
    def search(self, k, limit=10): return {"keyword":k, "total":0, "items":[]}
    def validate_session(self): return {"valid":True, "reason":"ok"}
    def health_check(self): return {"platform":"myplug", "status":"healthy"}
    def normalize(self, r):
        return SearchItem(platform="myplug", id=r.get("id",""), type="content", title="")
"""
        (plugins_dir / "my_plugin.py").write_text(plugin_code, encoding="utf-8")

        with patch.object(Path, "home", return_value=tmp_path):
            # 也 patch _load_adapters_from_file 确保 isolate
            result = _discover_filesystem()
            assert len(result) >= 1
            names = {a.platform_name for a in result}
            assert "myplug" in names

    def test_underscore_files_skipped(self, tmp_path: Path):
        """以下划线开头的文件被跳过。"""
        plugins_dir = tmp_path / ".cn-scraper-plugins"
        plugins_dir.mkdir()
        (plugins_dir / "_internal.py").write_text("import os", encoding="utf-8")

        with patch.object(Path, "home", return_value=tmp_path):
            result = _discover_filesystem()
            # _internal.py 不应产生有效的适配器
            names = {a.platform_name for a in result}
            assert "internal" not in names


# ── _load_adapters_from_file 测试 ──────────────────────────────


class TestLoadAdaptersFromFile:
    """测试从单个文件加载适配器的行为。"""

    def test_load_valid_adapter_file(self, tmp_path: Path):
        """包含有效适配器的文件被正确加载。"""
        plugin_code = """from cn_scraper_mcp.adapter import PlatformAdapter
from cn_scraper_mcp.models import SearchItem

class TestAdapter(PlatformAdapter):
    api_version = "0.6"
    schema_version = "1.0"
    @property
    def platform_name(self): return "testplug"
    def search(self, k, limit=10): return {"keyword":k, "total":0, "items":[]}
    def validate_session(self): return {"valid":True, "reason":"ok"}
    def health_check(self): return {"platform":"testplug", "status":"healthy"}
    def normalize(self, r):
        return SearchItem(platform="testplug", id=r.get("id",""), type="content", title="")

class NotAnAdapter:
    pass
"""
        file_path = tmp_path / "test_plugin.py"
        file_path.write_text(plugin_code, encoding="utf-8")

        adapters = _load_adapters_from_file(file_path)
        assert len(adapters) == 1
        assert adapters[0].platform_name == "testplug"

    def test_file_with_no_adapter_returns_empty(self, tmp_path: Path):
        """没有适配器类的文件返回空列表。"""
        file_path = tmp_path / "empty.py"
        file_path.write_text("x = 1\ny = 2\n", encoding="utf-8")

        adapters = _load_adapters_from_file(file_path)
        assert adapters == []

    def test_nonexistent_file_returns_empty(self, tmp_path: Path):
        """不存在的文件返回空。"""
        file_path = tmp_path / "does_not_exist.py"
        adapters = _load_adapters_from_file(file_path)
        assert adapters == []

    def test_syntax_error_file_returns_empty(self, tmp_path: Path):
        """语法错误的文件返回空列表（不抛异常）。"""
        file_path = tmp_path / "broken.py"
        file_path.write_text("this is not valid python !!!", encoding="utf-8")

        adapters = _load_adapters_from_file(file_path)
        assert adapters == []

    def test_adapter_with_init_error_skipped(self, tmp_path: Path):
        """实例化失败的适配器被跳过，但不影响其它有效适配器。"""
        plugin_code = """from cn_scraper_mcp.adapter import PlatformAdapter
from cn_scraper_mcp.models import SearchItem

class BadAdapter(PlatformAdapter):
    def __init__(self):
        raise RuntimeError("instantiation failed")

class GoodAdapter(PlatformAdapter):
    api_version = "0.6"
    schema_version = "1.0"
    @property
    def platform_name(self): return "goodplug"
    def search(self, k, limit=10): return {"keyword":k, "total":0, "items":[]}
    def validate_session(self): return {"valid":True, "reason":"ok"}
    def health_check(self): return {"platform":"goodplug", "status":"healthy"}
    def normalize(self, r):
        return SearchItem(platform="goodplug", id=r.get("id",""), type="content", title="")
"""
        file_path = tmp_path / "mixed.py"
        file_path.write_text(plugin_code, encoding="utf-8")

        adapters = _load_adapters_from_file(file_path)
        names = {a.platform_name for a in adapters}
        assert "goodplug" in names
        assert "badplug" not in names


class TestPluginIntegration:
    """集成级测试：验证插件发现流程端到端。"""

    def test_custom_group_name(self):
        """支持自定义 entry_points 组名。"""
        mock_entry = MagicMock()
        mock_entry.name = "custom_plugin"
        mock_entry.load.return_value = _ValidPluginAdapter

        with patch(
            "cn_scraper_mcp.plugin._discover_entry_points", return_value=[mock_entry]
        ):
            with patch("cn_scraper_mcp.plugin._discover_filesystem", return_value=[]):
                result = discover_plugins(group="my.custom.group")
                assert len(result) == 1

    def test_validate_session_returns_dict_checked(self):
        """validate_session 返回有效 dict 即可，不检查具体内容。"""

        class _OddSessionAdapter(_ValidPluginAdapter):
            @property
            def platform_name(self):
                return "odd_session"

            def validate_session(self):
                return {"valid": False, "reason": "no cookies"}

        adapter = _OddSessionAdapter()
        ok, reason = validate_plugin(adapter)
        assert ok is True

    def test_api_version_constants_defined(self):
        """API_VERSION 和 SCHEMA_VERSION 常量已定义且一致。"""
        assert isinstance(API_VERSION, str)
        assert isinstance(SCHEMA_VERSION, str)
        assert API_VERSION == "0.6"
        assert SCHEMA_VERSION == "1.0"

    def test_valid_adapter_has_expected_methods(self):
        """验证通过的适配器实现了 5 个必需方法。"""
        adapter = _ValidPluginAdapter()
        assert hasattr(adapter, "platform_name")
        assert callable(adapter.search)
        assert callable(adapter.validate_session)
        assert callable(adapter.health_check)
        assert callable(adapter.normalize)

    def test_api_version_minor_difference_ok(self):
        """API 版本 minor 不同但 major 相同应兼容。"""

        class _MinorDiff(_ValidPluginAdapter):
            api_version = "0.99"
            platform_name = "minor_diff"  # type: ignore[assignment]

        ok, reason = validate_plugin(_MinorDiff())
        assert ok is True


class TestPluginCapabilities:
    """验证插件 capabilities 可以在声明范围内操作。"""

    def test_valid_adapter_caps_are_dict_with_expected_keys(self):
        """capabilities 返回 dict 含 status/capabilities/requires_login。"""
        adapter = _ValidPluginAdapter()
        caps = adapter.capabilities
        assert isinstance(caps, dict)
        assert "status" in caps
        assert "capabilities" in caps
        assert "requires_login" in caps
        assert isinstance(caps["capabilities"], list)
        assert isinstance(caps["requires_login"], dict)

    def test_adapter_without_custom_caps_has_defaults(self):
        """未覆盖 capabilities 的适配器使用默认值。"""

        class _NoCaps(_ValidPluginAdapter):
            @property
            def platform_name(self):
                return "no_caps"

        adapter = _NoCaps()
        # 应继承 PlatformAdapter 的默认 capabilities
        caps = adapter.capabilities  # type: ignore[attr-defined]
        assert caps.get("status") is not None
