"""Tests for remote login verification without real network traffic."""

import json

import pytest

from cn_scraper_mcp import auth, auth_verify
from cn_scraper_mcp.auth_verify import verify_login
from cn_scraper_mcp.errors import ParseError, PlatformError


class FakeClient:
    def __init__(self, status: int, data: dict):
        self.status = status
        self.data = data
        self.calls = []

    def get_json(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.status, self.data


def _write_cookie(tmp_path, platform: str, data: dict) -> None:
    filename = auth.AUTH_PROFILES[platform].cookie_filename
    (tmp_path / filename).write_text(json.dumps(data), encoding="utf-8")


def test_zhihu_remote_login_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "zhihu", {"z_c0": "secret"})
    client = FakeClient(200, {"id": "account", "url_token": "token"})

    result = verify_login("zhihu", client=client)

    assert result["verified"] is True
    assert result["remote_state"] == "verified"
    assert client.calls[0][0].endswith("/api/v4/me")
    assert "secret" not in str(result)


def test_zhihu_identity_does_not_require_optional_url_token(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "zhihu", {"z_c0": "secret"})

    result = verify_login("zhihu", client=FakeClient(200, {"id": "account"}))

    assert result["verified"] is True


def test_weibo_remote_login_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "weibo", {"SUB": "secret"})

    result = verify_login("weibo", client=FakeClient(200, {"ok": -100}))

    assert result["verified"] is False
    assert result["remote_state"] == "rejected"


@pytest.mark.parametrize(
    ("platform", "cookies", "response", "expected"),
    [
        ("zhihu", {"z_c0": "secret"}, {"error": "html response"}, ParseError),
        ("weibo", {"SUB": "secret"}, {"error": "html response"}, PlatformError),
        ("zsxq", {"zsxq_access_token": "secret"}, {"succeeded": True}, ParseError),
        ("douyin", {"sessionid": "secret"}, {"status_code": 0}, ParseError),
    ],
)
def test_unexpected_success_response_is_not_mislabeled_as_rejected(
    tmp_path, monkeypatch, platform, cookies, response, expected
):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, platform, cookies)

    with pytest.raises(expected):
        verify_login(platform, client=FakeClient(200, response))


def test_douyin_explicit_logged_out_response_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "douyin", {"sessionid": "secret"})

    result = verify_login(
        "douyin",
        client=FakeClient(200, {"status_code": 8, "status_msg": "用户未登录"}),
    )

    assert result["remote_state"] == "rejected"
    assert result["verified"] is False


def test_douyin_server_error_is_not_mislabeled_as_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "douyin", {"sessionid": "secret"})

    with pytest.raises(PlatformError):
        verify_login("douyin", client=FakeClient(500, {"error": "server"}))


def test_remote_login_does_not_treat_missing_cache_as_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    client = FakeClient(200, {"id": "account", "url_token": "token"})

    result = verify_login("zhihu", client=client)

    assert result["remote_state"] == "local_invalid"
    assert client.calls == []


def test_remote_login_reports_unsupported_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(
        tmp_path,
        "taobao",
        {"_m_h5_tk": "a", "_tb_token_": "b", "cookie2": "c"},
    )

    result = verify_login("taobao", client=FakeClient(200, {}))

    assert result["remote_state"] == "unsupported"
    assert result["verified"] is False


def test_profile_platform_keeps_cache_state_in_contract(monkeypatch):
    monkeypatch.setattr(
        auth_verify,
        "check_all_cookies",
        lambda: {"jd": {"cache_state": "ready"}},
    )

    result = verify_login("jd")

    assert result["remote_state"] == "unsupported"
    assert result["cache_state"] == "ready"


def test_remote_transport_failure_is_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DEFAULT_COOKIE_DIR", tmp_path)
    _write_cookie(tmp_path, "zhihu", {"z_c0": "secret"})

    with pytest.raises(PlatformError):
        verify_login("zhihu", client=FakeClient(0, {"error": "timeout"}))
