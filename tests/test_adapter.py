"""Unit tests for adapter module — PlatformAdapter, CapabilityRegistry, TaobaoAdapter.

ALL mocks — no real network, filesystem, or Chrome.
"""

from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.adapter import (
    CapabilityRegistry,
    PlatformAdapter,
    TaobaoAdapter,
    generate_health_check_params,
    generate_readme_matrix,
    generate_test_params,
)
from cn_scraper_mcp.models import ProductItem

# ── Minimal concrete adapter for testing ABC ─────────────────────


class _FakeAdapter(PlatformAdapter):
    """A minimal concrete adapter used in ABC and registry tests."""

    @property
    def platform_name(self) -> str:
        return "fake"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {"keyword": keyword, "items": []}

    def validate_session(self) -> dict:
        return {"valid": True, "reason": "ok"}

    def health_check(self) -> dict:
        return {"platform": "fake", "status": "healthy", "reason": None, "latency_ms": 1.0, "adapter_version": "v0.1.0"}

    def normalize(self, raw_item: dict) -> ProductItem:
        return ProductItem(platform="fake", id=raw_item.get("id", ""), type="product", title=raw_item.get("title", ""))


class _AnotherAdapter(PlatformAdapter):
    """A second minimal adapter for multi-registry tests."""

    @property
    def platform_name(self) -> str:
        return "another"

    def search(self, keyword: str, limit: int = 10) -> dict:
        return {"keyword": keyword, "items": []}

    def validate_session(self) -> dict:
        return {"valid": False, "reason": "no cookie"}

    def health_check(self) -> dict:
        return {"platform": "another", "status": "degraded", "reason": "session_expired", "latency_ms": 2.0, "adapter_version": "v0.1.0"}

    def normalize(self, raw_item: dict) -> ProductItem:
        return ProductItem(platform="another", id=raw_item.get("id", ""), type="product", title=raw_item.get("title", ""))

    @property
    def capabilities(self) -> dict:
        return {"status": "experimental", "capabilities": ["search", "hot_list"], "requires_login": {"search": False}}


# ── PlatformAdapter ABC tests ───────────────────────────────────


class TestPlatformAdapterABC:
    """Test that PlatformAdapter ABC enforces its contract."""

    def test_cannot_instantiate_abc_directly(self):
        """Instantiating PlatformAdapter directly should raise TypeError."""
        with pytest.raises(TypeError):
            PlatformAdapter()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self):
        """A fully-implemented subclass must instantiate without error."""
        adapter = _FakeAdapter()
        assert adapter.platform_name == "fake"

    def test_partial_subclass_cannot_instantiate(self):
        """A subclass missing one abstract method should fail."""

        class Incomplete(PlatformAdapter):
            @property
            def platform_name(self):
                return "incomplete"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_default_capabilities(self):
        """Default capabilities property returns expected structure."""
        adapter = _FakeAdapter()
        caps = adapter.capabilities
        assert caps["status"] == "stable"
        assert "capabilities" in caps
        assert "requires_login" in caps

    def test_default_timeout_seconds(self):
        """Default timeout_seconds is 30."""
        adapter = _FakeAdapter()
        assert adapter.timeout_seconds == 30

    def test_default_supports_concurrency_is_false(self):
        """Default supports_concurrency is False."""
        adapter = _FakeAdapter()
        assert adapter.supports_concurrency is False


# ── CapabilityRegistry tests ────────────────────────────────────


class TestCapabilityRegistry:
    """Test CapabilityRegistry registration, lookup, and matrix generation."""

    @pytest.fixture
    def registry(self):
        """Create a fresh registry with two adapters."""
        reg = CapabilityRegistry()
        reg.register(_FakeAdapter())
        reg.register(_AnotherAdapter())
        return reg

    def test_register_adds_adapter(self):
        """register() adds adapter and is retrievable by get()."""
        reg = CapabilityRegistry()
        adapter = _FakeAdapter()
        reg.register(adapter)
        assert reg.get("fake") is adapter

    def test_register_duplicate_raises(self, registry):
        """Registering the same platform name twice raises ValueError."""
        with pytest.raises(ValueError, match="already registered"):
            registry.register(_FakeAdapter())

    def test_unregister_removes_adapter(self, registry):
        """unregister() removes the adapter from the registry."""
        registry.unregister("fake")
        with pytest.raises(KeyError):
            registry.get("fake")

    def test_unregister_missing_raises(self, registry):
        """Unregistering an unregistered platform raises KeyError."""
        with pytest.raises(KeyError, match="not registered"):
            registry.unregister("nonexistent")

    def test_get_returns_correct_adapter(self, registry):
        """get() returns the PlatformAdapter for the given platform."""
        adapter = registry.get("fake")
        assert adapter.platform_name == "fake"
        assert isinstance(adapter, _FakeAdapter)

    def test_get_missing_raises(self, registry):
        """get() on an unregistered platform raises KeyError."""
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nonexistent")

    def test_list_all_returns_all_adapters(self, registry):
        """list_all() returns all registered PlatformAdapter instances."""
        all_adapters = registry.list_all()
        assert len(all_adapters) == 2
        names = {a.platform_name for a in all_adapters}
        assert names == {"fake", "another"}

    def test_platforms_returns_sorted_names(self, registry):
        """platforms() returns a sorted list of platform names."""
        names = registry.platforms()
        assert names == ["another", "fake"]

    def test_capability_matrix_structure(self, registry):
        """capability_matrix() returns a dict with platform capabilities."""
        matrix = registry.capability_matrix()
        assert "platforms" in matrix
        platforms = matrix["platforms"]
        assert "fake" in platforms
        assert "another" in platforms

        fake_caps = platforms["fake"]
        assert fake_caps["status"] == "stable"
        assert fake_caps["capabilities"] == ["search"]
        assert fake_caps["requires_login"] == {"search": True}
        assert fake_caps["timeout_seconds"] == 30
        assert fake_caps["supports_concurrency"] is False

        another_caps = platforms["another"]
        assert another_caps["status"] == "experimental"
        assert "hot_list" in another_caps["capabilities"]

    def test_empty_registry(self):
        """All operations on an empty registry should return sensible defaults."""
        reg = CapabilityRegistry()
        assert reg.platforms() == []
        assert reg.list_all() == []
        assert reg.capability_matrix() == {"platforms": {}}

    def test_register_and_unregister_restores_empty(self, registry):
        """After unregistering all, registry should be empty."""
        registry.unregister("fake")
        registry.unregister("another")
        assert registry.platforms() == []
        assert registry.list_all() == []


# ── Auto-generation helper tests ────────────────────────────────


class TestGenerateReadmeMatrix:
    """Test generate_readme_matrix output."""

    @pytest.fixture
    def registry(self):
        reg = CapabilityRegistry()
        reg.register(_FakeAdapter())
        reg.register(_AnotherAdapter())
        return reg

    def test_returns_markdown_string(self, registry):
        """generate_readme_matrix returns a Markdown-formatted string."""
        result = generate_readme_matrix(registry)
        assert isinstance(result, str)
        assert "| 平台 |" in result
        assert "|------|" in result

    def test_includes_all_platforms(self, registry):
        """Matrix includes all registered platforms."""
        result = generate_readme_matrix(registry)
        assert "fake" in result
        assert "another" in result

    def test_includes_status_labels(self, registry):
        """Matrix includes status labels for each platform."""
        result = generate_readme_matrix(registry)
        assert "稳定" in result
        assert "实验性" in result

    def test_empty_registry_produces_header_only(self):
        """Empty registry produces just the header row."""
        reg = CapabilityRegistry()
        result = generate_readme_matrix(reg)
        lines = result.strip().split("\n")
        assert len(lines) == 2  # header + separator only


class TestGenerateHealthCheckParams:
    """Test generate_health_check_params output."""

    @pytest.fixture
    def registry(self):
        reg = CapabilityRegistry()
        reg.register(_FakeAdapter())
        reg.register(_AnotherAdapter())
        return reg

    def test_returns_dict_with_platforms_key(self, registry):
        """Returns a dict with top-level 'platforms' key."""
        result = generate_health_check_params(registry)
        assert "platforms" in result
        assert isinstance(result["platforms"], dict)

    def test_includes_all_platforms(self, registry):
        """Health check params cover all registered platforms."""
        result = generate_health_check_params(registry)
        platforms = result["platforms"]
        assert "fake" in platforms
        assert "another" in platforms

    def test_platform_entry_has_expected_keys(self, registry):
        """Each platform entry has timeout, concurrency, capabilities."""
        result = generate_health_check_params(registry)
        entry = result["platforms"]["fake"]
        assert "cookie_platform" in entry
        assert "timeout_seconds" in entry
        assert "supports_concurrency" in entry
        assert "capabilities" in entry

    def test_empty_registry(self):
        """Empty registry returns empty dict."""
        reg = CapabilityRegistry()
        result = generate_health_check_params(reg)
        assert result == {"platforms": {}}


class TestGenerateTestParams:
    """Test generate_test_params output."""

    @pytest.fixture
    def registry(self):
        reg = CapabilityRegistry()
        reg.register(_FakeAdapter())
        reg.register(_AnotherAdapter())
        return reg

    def test_returns_dict_with_platforms_and_test_configs(self, registry):
        """Returns a dict with 'platforms' list and 'test_configs' dict."""
        result = generate_test_params(registry)
        assert "platforms" in result
        assert "test_configs" in result

    def test_platforms_is_sorted_list(self, registry):
        """platforms is a sorted list of names."""
        result = generate_test_params(registry)
        assert result["platforms"] == ["another", "fake"]

    def test_test_configs_match_adapters(self, registry):
        """test_configs includes per-platform expected methods and timeout."""
        result = generate_test_params(registry)
        configs = result["test_configs"]
        assert configs["fake"]["expected_methods"] == ["search"]
        assert configs["another"]["expected_methods"] == ["search", "hot_list"]

    def test_empty_registry(self):
        """Empty registry returns empty lists/dicts."""
        reg = CapabilityRegistry()
        result = generate_test_params(reg)
        assert result == {"platforms": [], "test_configs": {}}


# ── TaobaoAdapter tests ─────────────────────────────────────────


class TestTaobaoAdapter:
    """Test TaobaoAdapter wrapping TaobaoEngine."""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock TaobaoEngine by patching the import target."""
        with patch(
            "cn_scraper_mcp.engines.taobao.TaobaoEngine", autospec=True
        ) as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.cookies = {"_m_h5_tk": "abc123_test_token"}
            yield mock_inst

    @pytest.fixture
    def adapter(self, mock_engine):
        """Create a TaobaoAdapter with mocked engine."""
        return TaobaoAdapter()

    def test_platform_name(self, adapter):
        """platform_name is 'taobao'."""
        assert adapter.platform_name == "taobao"

    def test_search_delegates_to_engine(self, adapter, mock_engine):
        """search() delegates to TaobaoEngine.search()."""
        mock_engine.search.return_value = {
            "keyword": "test",
            "total": 5,
            "items": [{"title": "item1"}],
        }
        result = adapter.search("test", limit=5)
        mock_engine.search.assert_called_once_with("test", limit=5)
        assert result["keyword"] == "test"
        assert result["total"] == 5

    def test_validate_session_no_cookie_file(self, adapter):
        """validate_session returns valid=False when cookie file missing."""
        with patch("os.path.exists", return_value=False):
            result = adapter.validate_session()
            assert result["valid"] is False
            assert "不存在" in result["reason"]

    def test_validate_session_cookie_exists(self, adapter):
        """validate_session returns valid=True with proper cookie."""
        with (
            patch("pathlib.Path.home", return_value=MagicMock()),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", MagicMock()),
            patch("json.load", return_value={"_m_h5_tk": "token123"}),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = adapter.validate_session()
            assert result["valid"] is True
            assert "存在" in result["reason"]

    def test_validate_session_missing_token(self, adapter):
        """validate_session returns valid=False when _m_h5_tk missing."""
        with (
            patch("pathlib.Path.home", return_value=MagicMock()),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", MagicMock()),
            patch("json.load", return_value={"other": "value"}),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = adapter.validate_session()
            assert result["valid"] is False
            assert "_m_h5_tk" in result["reason"]

    def test_validate_session_bad_json(self, adapter):
        """validate_session returns valid=False on corrupt JSON."""
        with (
            patch("pathlib.Path.home", return_value=MagicMock()),
            patch("os.path.exists", return_value=True),
            patch("builtins.open", MagicMock()),
            patch("json.load", side_effect=ValueError("bad json")),
            patch.dict("os.environ", {}, clear=True),
        ):
            result = adapter.validate_session()
            assert result["valid"] is False
            assert "无法解析" in result["reason"]

    def test_health_check_healthy(self, adapter, mock_engine):
        """health_check returns healthy with valid token."""
        mock_engine.cookies = {"_m_h5_tk": "token123"}
        result = adapter.health_check()
        assert result["platform"] == "taobao"
        assert result["status"] == "healthy"
        assert result["reason"] is None
        assert "adapter_version" in result
        assert "latency_ms" in result

    def test_health_check_degraded_no_token(self, adapter, mock_engine):
        """health_check returns degraded when token missing."""
        mock_engine.cookies = {}
        result = adapter.health_check()
        assert result["status"] == "degraded"
        assert result["reason"] == "session_expired"

    def test_normalize_returns_product_item(self, adapter):
        """normalize() converts raw item to ProductItem."""
        raw = {
            "id": "1234",
            "title": "测试商品",
            "price": "99.00",
            "origPrice": "199.00",
            "sales": "100+",
            "shop": "测试店铺",
            "url": "https://item.taobao.com/item.htm?id=1234",
        }
        item = adapter.normalize(raw)
        assert isinstance(item, ProductItem)
        assert item.platform == "taobao"
        assert item.id == "1234"
        assert item.title == "测试商品"
        assert item.price == 99.0
        assert item.orig_price == 199.0
        assert item.shop == "测试店铺"

    def test_capabilities(self, adapter):
        """capabilities property returns correct structure for taobao."""
        caps = adapter.capabilities
        assert caps["status"] == "stable"
        assert "search" in caps["capabilities"]
        assert "item_detail" in caps["capabilities"]
        assert caps["requires_login"] == {"search": True, "item_detail": True}

    def test_timeout_seconds(self, adapter):
        """taobao timeout is 15 seconds."""
        assert adapter.timeout_seconds == 15

    def test_supports_concurrency(self, adapter):
        """taobao supports concurrency (pure HTTP API)."""
        assert adapter.supports_concurrency is True
