"""Unit tests for auth.py — CookieFileManager and check_all_cookies.

ALL mocks — no real filesystem, no real cookie files.
NEVER asserts on cookie VALUES — only field names and metadata.
"""

import dataclasses
import datetime
import json
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from cn_scraper_mcp.auth import (
    AUTH_PROFILES,
    STALE_HOURS,
    AuthProfile,
    CookieFileManager,
    CredentialCacheState,
    _check_jd_profile,
    check_all_cookies,
)

# ═══════════════════════════════════════════════════════════════
# CookieFileManager — path resolution
# ═══════════════════════════════════════════════════════════════

class TestCookieFileManagerPathResolution:
    """Path resolution: explicit > env var > default."""

    def test_explicit_path_wins(self, monkeypatch):
        """Explicit path passed to __init__ takes highest priority."""
        monkeypatch.setenv("TAOBAO_COOKIES_FILE", "/env/taobao.json")
        # Use a Windows-aware path to avoid drive-letter differences from .resolve()
        explicit = Path("C:/explicit/taobao.json")
        mgr = CookieFileManager("taobao", cookies_path=str(explicit))
        assert str(mgr.resolve_path()) == str(explicit.resolve())

    def test_env_var_when_no_explicit(self, monkeypatch):
        """When no explicit path, env var is used."""
        env_path = Path("C:/env/taobao.json")
        monkeypatch.setenv("TAOBAO_COOKIES_FILE", str(env_path))
        mgr = CookieFileManager("taobao")
        assert str(mgr.resolve_path()) == str(env_path.resolve())

    def test_default_path_fallback(self, monkeypatch):
        """When no explicit path and no env var, use ~/.cn-scraper-cookies/<name>.json."""
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        mgr = CookieFileManager("taobao")
        resolved = mgr.resolve_path()
        assert resolved.name == "taobao.json"
        assert ".cn-scraper-cookies" in str(resolved)

    def test_unknown_platform_raises(self):
        """Passing an unknown platform raises ValueError."""
        with pytest.raises(ValueError, match="Unknown platform"):
            CookieFileManager("not_a_platform")


# ═══════════════════════════════════════════════════════════════
# CookieFileManager — field validation
# ═══════════════════════════════════════════════════════════════

class TestCookieFileManagerValidation:
    """Required-field validation per platform."""

    def test_taobao_all_required_present(self):
        mgr = CookieFileManager("taobao", cookies_path="/tmp/t.json")
        missing = mgr.validate({
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        })
        assert missing == []

    def test_taobao_missing_one_field(self):
        mgr = CookieFileManager("taobao", cookies_path="/tmp/t.json")
        missing = mgr.validate({
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
            # cookie2 missing
        })
        assert missing == ["cookie2"]

    def test_taobao_empty_string_treated_as_missing(self):
        mgr = CookieFileManager("taobao", cookies_path="/tmp/t.json")
        missing = mgr.validate({
            "_m_h5_tk": "hidden",
            "_tb_token_": "",
            "cookie2": "hidden",
        })
        assert "_tb_token_" in missing

    def test_taobao_none_treated_as_missing(self):
        mgr = CookieFileManager("taobao", cookies_path="/tmp/t.json")
        missing = mgr.validate({
            "_m_h5_tk": None,
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        })
        assert "_m_h5_tk" in missing

    def test_xiaohongshu_required_fields(self):
        mgr = CookieFileManager("xiaohongshu", cookies_path="/tmp/x.json")
        missing = mgr.validate({"web_session": "hidden", "a1": "hidden"})
        assert missing == []

        missing = mgr.validate({"web_session": "hidden"})
        assert missing == ["a1"]

    def test_zhihu_required_fields(self):
        mgr = CookieFileManager("zhihu", cookies_path="/tmp/z.json")
        missing = mgr.validate({"z_c0": "hidden"})
        assert missing == []

        missing = mgr.validate({})
        assert missing == ["z_c0"]

    def test_zsxq_required_fields(self):
        mgr = CookieFileManager("zsxq", cookies_path="/tmp/zs.json")
        missing = mgr.validate({"zsxq_access_token": "hidden"})
        assert missing == []

        missing = mgr.validate({"zsxq_access_token": ""})
        assert missing == ["zsxq_access_token"]


# ═══════════════════════════════════════════════════════════════
# CookieFileManager — check() (file exists + field validation)
# ═══════════════════════════════════════════════════════════════

class TestCookieFileManagerCheck:
    """check() method returns full status dict."""

    def test_file_not_exists(self, monkeypatch):
        """When file doesn't exist, exists=False and all required fields reported missing."""
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        mgr = CookieFileManager("taobao", cookies_path="/nonexistent/file.json")
        result = mgr.check()
        assert result["exists"] is False
        assert result["valid"] is False
        assert set(result["missing_fields"]) == {"_m_h5_tk", "_tb_token_", "cookie2"}
        assert result["age_hours"] is None
        assert result["stale"] is False

    def test_file_exists_all_fields_valid(self):
        """File exists with all required fields → valid=True."""
        cookie_data = {
            "_m_h5_tk": "hidden",
            "_tb_token_": "hidden",
            "cookie2": "hidden",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = (
                datetime.datetime.now() - datetime.timedelta(hours=1)
            ).timestamp()

            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()

            assert result["exists"] is True
            assert result["valid"] is True
            assert result["missing_fields"] == []
            assert result["age_hours"] <= 1.1
            assert result["stale"] is False

    def test_file_exists_missing_fields(self):
        """File exists but missing required fields → valid=False."""
        cookie_data = {"_m_h5_tk": "hidden"}  # missing _tb_token_ and cookie2

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()

            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()

            assert result["exists"] is True
            assert result["valid"] is False
            assert set(result["missing_fields"]) == {"_tb_token_", "cookie2"}

    def test_stale_when_old(self):
        """File older than STALE_HOURS → stale=True."""
        cookie_data = {
            "_m_h5_tk": "hidden", "_tb_token_": "hidden", "cookie2": "hidden",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))
        old_time = (
            datetime.datetime.now() - datetime.timedelta(hours=STALE_HOURS + 10)
        ).timestamp()

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = old_time

            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()

            assert result["stale"] is True
            assert result["age_hours"] >= STALE_HOURS

    def test_unreadable_json_returns_error_status(self):
        """Invalid JSON → valid=False with error message in missing_fields."""
        m_open = mock_open(read_data="not valid json {{{")

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):

            mgr = CookieFileManager("taobao", cookies_path="/fake/bad.json")
            result = mgr.check()

            assert result["exists"] is True
            assert result["valid"] is False
            assert "<file unreadable" in result["missing_fields"][0]

    def test_result_never_contains_cookie_values(self):
        """CRITICAL: check() output must never contain cookie values."""
        cookie_data = {
            "_m_h5_tk": "secret_token_value",
            "_tb_token_": "another_secret",
            "cookie2": "super_secret_cookie",
        }

        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()

            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()

            # Flatten all string values and verify no secret is leaked
            def flatten_strs(d):
                for v in d.values():
                    if isinstance(v, str):
                        yield v
                    elif isinstance(v, list):
                        yield from v

            all_strings = " ".join(flatten_strs(result))
            assert "secret_token_value" not in all_strings
            assert "another_secret" not in all_strings
            assert "super_secret_cookie" not in all_strings


# ═══════════════════════════════════════════════════════════════
# CookieFileManager — context manager
# ═══════════════════════════════════════════════════════════════

class TestCookieFileManagerContextManager:
    """Context manager opens and closes file properly."""

    def test_context_manager_reads_data(self):
        cookie_data = {"_m_h5_tk": "hidden", "_tb_token_": "hidden", "cookie2": "hidden"}

        with patch("builtins.open", mock_open()), \
             patch.object(Path, "exists", return_value=True), \
             patch("json.load", return_value=cookie_data):

            with CookieFileManager("taobao", cookies_path="/fake/taobao.json") as mgr:
                assert mgr.data is not None
                assert "_m_h5_tk" in mgr.data

    def test_context_manager_nonexistent_file_data_is_none(self):
        with patch("builtins.open", mock_open()), \
             patch.object(Path, "exists", return_value=False):

            with CookieFileManager("taobao", cookies_path="/fake/taobao.json") as mgr:
                assert mgr.data is None

    def test_data_non_dict_json_root_returns_none(self):
        with patch("builtins.open", mock_open()), \
             patch.object(Path, "exists", return_value=True), \
             patch("json.load", return_value=[]):

            with CookieFileManager("taobao", cookies_path="/fake/taobao.json") as mgr:
                assert mgr.data is None


# ═══════════════════════════════════════════════════════════════
# check_all_cookies
# ═══════════════════════════════════════════════════════════════

class TestCheckAllCookies:
    """check_all_cookies() aggregates all platforms."""

    def test_returns_all_platform_keys(self):
        """Result dict must contain all platform keys."""
        with patch.object(CookieFileManager, "check", return_value={
            "exists": False, "valid": False, "missing_fields": ["x"],
            "path": "/fake", "mtime": None, "age_hours": None, "stale": False,
        }), patch("cn_scraper_mcp.auth._check_jd_profile", return_value={
            "exists": False, "valid": False, "missing_fields": [],
            "path": "/none", "mtime": None, "age_hours": None, "stale": False,
        }):
            result = check_all_cookies()

            assert "taobao" in result
            assert "xiaohongshu" in result
            assert "zhihu" in result
            assert "zsxq" in result
            assert "jd" in result
            assert "pdd" in result
            assert "weibo" in result
            assert "douyin" in result
            assert "douban" in result
            assert "dianping" in result
            assert len(result) == 10

    def test_never_leaks_values(self):
        """check_all_cookies output must never contain cookie values."""
        with patch.object(CookieFileManager, "check", return_value={
            "exists": False, "valid": False, "missing_fields": ["_m_h5_tk"],
            "path": "/fake", "mtime": None, "age_hours": None, "stale": False,
        }), patch("cn_scraper_mcp.auth._check_jd_profile", return_value={
            "exists": False, "valid": False, "missing_fields": [],
            "path": "/none", "mtime": None, "age_hours": None, "stale": False,
        }):
            result = check_all_cookies()

            # Flatten all strings and verify no secret-looking content
            def collect_strings(d, depth=0):
                if depth > 10:
                    return
                if isinstance(d, dict):
                    for v in d.values():
                        yield from collect_strings(v, depth + 1)
                elif isinstance(d, list):
                    for item in d:
                        if isinstance(item, str):
                            yield item
                elif isinstance(d, str):
                    yield d

            all_text = " ".join(collect_strings(result))
            # field names are fine; actual values like "abc123def" should not appear
            assert "secret" not in all_text.lower()


# ═══════════════════════════════════════════════════════════════
# _check_jd_profile — JD special case
# ═══════════════════════════════════════════════════════════════

class TestJDProfile:
    """JD uses Chrome profile dir, not a JSON cookie file."""

    def test_jd_profile_exists(self):
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "stat") as mock_stat:

            mock_stat.return_value.st_mtime = (
                datetime.datetime.now() - datetime.timedelta(hours=1)
            ).timestamp()

            result = _check_jd_profile()
            assert result["exists"] is True
            assert result["valid"] is True
            assert result["type"] == "chrome_profile_dir"
            assert result["stale"] is False
            assert result["age_hours"] <= 1.1

    def test_jd_profile_missing(self):
        with patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "is_dir", return_value=False):

            result = _check_jd_profile()
            assert result["exists"] is False
            assert result["valid"] is False
            assert result["age_hours"] is None

    def test_jd_profile_exists_but_not_directory(self):
        """If .jd_login_profile is a file not a dir, treat as invalid."""
        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "is_dir", return_value=False):

            result = _check_jd_profile()
            assert result["exists"] is False  # is_dir check fails


# ═══════════════════════════════════════════════════════════════
# AuthProfile — frozen dataclass
# ═══════════════════════════════════════════════════════════════

class TestAuthProfile:
    """AuthProfile is a frozen dataclass with auth-only fields (no business fields)."""

    def test_is_frozen(self):
        """AuthProfile must be immutable."""
        profile = AuthProfile(
            platform="test",
            cookie_filename="test.json",
            env_var="TEST_COOKIES",
            required_fields=("a", "b"),
            login_url="https://test.com/login",
            cookie_domain=".test.com",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.platform = "changed"  # type: ignore[misc]

    def test_contains_no_business_fields(self):
        """AuthProfile must NOT contain API endpoint, search params, DOM selectors, etc."""
        fields = {f.name for f in dataclasses.fields(AuthProfile)}
        business_fields = {
            "api_endpoint", "search_params", "dom_selector",
            "product_fields", "hot_list_fields", "comment_structure",
            "retry_rules",
        }
        assert fields.isdisjoint(business_fields), \
            f"AuthProfile must not contain business fields: {fields & business_fields}"

    def test_all_platforms_in_auth_profiles(self):
        """All supported platforms must be in AUTH_PROFILES."""
        expected = {"taobao", "xiaohongshu", "zhihu", "zsxq", "pdd", "weibo", "douyin", "jd", "douban", "dianping"}
        assert set(AUTH_PROFILES.keys()) == expected

    def test_douyin_requires_session_cookie(self):
        assert AUTH_PROFILES["douyin"].required_fields == ("sessionid",)

    def test_non_profile_platforms_have_cookie_filename(self):
        """Non-profile platforms must have a cookie_filename."""
        for name, profile in AUTH_PROFILES.items():
            if not profile.is_profile:
                assert profile.cookie_filename, f"{name}: cookie_filename must not be empty"
                assert profile.env_var, f"{name}: env_var must not be empty"

    def test_jd_is_profile(self):
        """JD is the only profile-based platform."""
        assert AUTH_PROFILES["jd"].is_profile is True
        assert AUTH_PROFILES["jd"].cookie_filename == ""

    def test_login_signal_present_for_all(self):
        """Every platform should have at least one login signal cookie."""
        for name, profile in AUTH_PROFILES.items():
            assert len(profile.login_signal) > 0, f"{name}: must have login_signal cookies"


# ═══════════════════════════════════════════════════════════════
# CredentialCacheState enum
# ═══════════════════════════════════════════════════════════════

class TestCredentialCacheState:
    """CredentialCacheState enum covers all local cache outcomes."""

    def test_all_five_states_exist(self):
        states = set(CredentialCacheState)
        assert states == {
            CredentialCacheState.MISSING,
            CredentialCacheState.MALFORMED,
            CredentialCacheState.INCOMPLETE,
            CredentialCacheState.STALE,
            CredentialCacheState.READY,
        }

    def test_string_values_are_lowercase(self):
        for state in CredentialCacheState:
            assert state.value == state.value.lower()
            assert " " not in state.value


# ═══════════════════════════════════════════════════════════════
# cache_state in check() output
# ═══════════════════════════════════════════════════════════════

class TestCheckCacheState:
    """check() output includes the correct cache_state for each scenario."""

    def test_missing_file_returns_cache_state_missing(self, monkeypatch):
        monkeypatch.delenv("TAOBAO_COOKIES_FILE", raising=False)
        mgr = CookieFileManager("taobao", cookies_path="/nonexistent/file.json")
        result = mgr.check()
        assert result["cache_state"] == CredentialCacheState.MISSING.value

    def test_ready_file_returns_cache_state_ready(self):
        cookie_data = {"_m_h5_tk": "x", "_tb_token_": "x", "cookie2": "x"}
        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()
            assert result["cache_state"] == CredentialCacheState.READY.value

    def test_incomplete_fields_returns_cache_state_incomplete(self):
        cookie_data = {"_m_h5_tk": "x"}  # missing _tb_token_, cookie2
        m_open = mock_open(read_data=json.dumps(cookie_data))

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mtime = datetime.datetime.now().timestamp()
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()
            assert result["cache_state"] == CredentialCacheState.INCOMPLETE.value

    def test_stale_file_returns_cache_state_stale(self):
        cookie_data = {"_m_h5_tk": "x", "_tb_token_": "x", "cookie2": "x"}
        m_open = mock_open(read_data=json.dumps(cookie_data))
        old_time = (
            datetime.datetime.now() - datetime.timedelta(hours=STALE_HOURS + 1)
        ).timestamp()

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "stat") as mock_stat:
            mock_stat.return_value.st_mtime = old_time
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()
            assert result["cache_state"] == CredentialCacheState.STALE.value

    def test_malformed_json_returns_cache_state_malformed(self):
        m_open = mock_open(read_data="not valid json {{{")

        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):
            mgr = CookieFileManager("taobao", cookies_path="/fake/bad.json")
            result = mgr.check()
            assert result["cache_state"] == CredentialCacheState.MALFORMED.value

    @pytest.mark.parametrize("non_dict_json", [
        "null",
        "[]",
        '"just a string"',
        "42",
        "true",
    ])
    def test_non_dict_json_root_returns_cache_state_malformed(self, non_dict_json):
        m_open = mock_open(read_data=non_dict_json)
        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.check()
            assert result["cache_state"] == CredentialCacheState.MALFORMED.value


# ═══════════════════════════════════════════════════════════════
# CookieFileManager.load() — one-shot reads
# ═══════════════════════════════════════════════════════════════

class TestCookieFileManagerLoad:
    """load() returns a dict or empty dict — never raises."""

    def test_load_returns_cookie_dict(self):
        cookie_data = {"_m_h5_tk": "x", "_tb_token_": "x", "cookie2": "x"}
        m_open = mock_open(read_data=json.dumps(cookie_data))
        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.load()
            assert result == cookie_data

    def test_load_missing_file_returns_empty_dict(self):
        with patch.object(Path, "exists", return_value=False):
            mgr = CookieFileManager("taobao", cookies_path="/fake/missing.json")
            result = mgr.load()
            assert result == {}

    def test_load_invalid_json_returns_empty_dict(self):
        m_open = mock_open(read_data="not json")
        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):
            mgr = CookieFileManager("taobao", cookies_path="/fake/bad.json")
            result = mgr.load()
            assert result == {}

    @pytest.mark.parametrize("non_dict_json", [
        "null",
        "[]",
        '"just a string"',
        "42",
        "true",
    ])
    def test_load_non_dict_json_root_returns_empty_dict(self, non_dict_json):
        m_open = mock_open(read_data=non_dict_json)
        with patch("builtins.open", m_open), \
             patch.object(Path, "exists", return_value=True):
            mgr = CookieFileManager("taobao", cookies_path="/fake/taobao.json")
            result = mgr.load()
            assert result == {}
