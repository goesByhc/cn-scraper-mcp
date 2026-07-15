"""Unit tests for canary runner.

ALL mocks — no real network, credentials, or Chrome.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from scripts.canary_runner import (
    CanaryConfig,
    CanaryResult,
    _clear_failure_counter,
    _consecutive_failures,
    _mock_douyin,
    _mock_jd,
    _mock_pdd,
    _mock_taobao,
    _mock_weibo,
    _mock_xiaohongshu,
    _mock_zhihu,
    _mock_zsxq,
    _record_failure,
    build_diagnostics,
    format_issue_template,
    run_all,
    run_canary,
    sanitise,
)

# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_failure_counters():
    _consecutive_failures.clear()
    yield
    _consecutive_failures.clear()


def _cfg(key: str = "test", max_failures: int = 3) -> CanaryConfig:
    return CanaryConfig(key, key, 10, 5.0, max_failures, lambda: {"ok": True})


# ═══════════════════════════════════════════════════════════════
# Mock queries — return shape
# ═══════════════════════════════════════════════════════════════


def test_mock_taobao_returns_expected_shape():
    r = _mock_taobao()
    assert r["platform"] == "taobao"
    assert r["mock"] is True
    assert "items" in r


def test_mock_jd_returns_expected_shape():
    r = _mock_jd()
    assert r["platform"] == "jd"
    assert r["mock"] is True


def test_mock_pdd_returns_expected_shape():
    r = _mock_pdd()
    assert r["platform"] == "pdd"
    assert r["mock"] is True


def test_mock_xiaohongshu_returns_expected_shape():
    r = _mock_xiaohongshu()
    assert r["platform"] == "xiaohongshu"
    assert r["mock"] is True


def test_mock_zhihu_returns_expected_shape():
    r = _mock_zhihu()
    assert r["platform"] == "zhihu"
    assert r["mock"] is True


def test_mock_zsxq_returns_expected_shape():
    r = _mock_zsxq()
    assert r["platform"] == "zsxq"
    assert r["mock"] is True


def test_mock_weibo_returns_expected_shape():
    r = _mock_weibo()
    assert r["platform"] == "weibo"
    assert r["mock"] is True


def test_mock_douyin_returns_expected_shape():
    r = _mock_douyin()
    assert r["platform"] == "douyin"
    assert r["mock"] is True


# ═══════════════════════════════════════════════════════════════
# run_canary — success path
# ═══════════════════════════════════════════════════════════════


def test_run_canary_success():
    cfg = _cfg("taobao")
    result = run_canary(cfg)
    assert result.ok is True
    assert result.platform == "taobao"
    assert result.duration_ms >= 0
    assert result.error_type == ""
    assert result.error_message == ""
    assert result.timestamp != ""


def test_run_canary_sets_timestamp():
    cfg = _cfg()
    result = run_canary(cfg)
    assert result.timestamp.endswith("+00:00") or "T" in result.timestamp


# ═══════════════════════════════════════════════════════════════
# run_canary — failure paths
# ═══════════════════════════════════════════════════════════════


def test_run_canary_exception_captures_error():
    def _failing():
        raise ValueError("boom")

    cfg = CanaryConfig("bad", "bad", 10, 5.0, 3, _failing)
    result = run_canary(cfg)
    assert result.ok is False
    assert result.error_type == "ValueError"
    assert "boom" in result.error_message


def test_run_canary_timeout_captured():
    def _timeout():
        raise TimeoutError("timed out after 5s")

    cfg = CanaryConfig("slow", "slow", 10, 1.0, 3, _timeout)
    result = run_canary(cfg)
    assert result.ok is False
    assert result.error_type == "Timeout"


def test_run_canary_connection_error_captured():
    def _conn():
        raise ConnectionError("refused")

    cfg = CanaryConfig("conn", "conn", 10, 5.0, 3, _conn)
    result = run_canary(cfg)
    assert result.ok is False
    assert result.error_type == "ConnectionError"


def test_run_canary_non_dict_response_captured():
    def _bad():
        return "not a dict"

    cfg = CanaryConfig("bad", "bad", 10, 5.0, 3, _bad)
    result = run_canary(cfg)
    assert result.ok is False
    assert result.error_type == "MalformedResponse"
    assert "str" in result.error_message


# ═══════════════════════════════════════════════════════════════
# run_all — orchestration
# ═══════════════════════════════════════════════════════════════


def test_run_all_all_platforms():
    report = run_all()
    assert report.total_platforms == 8
    assert report.passed == 8
    assert report.failed == 0
    assert report.alerts == []


def test_run_all_single_platform():
    report = run_all(["taobao"])
    assert report.total_platforms == 1
    assert report.passed == 1
    assert report.failed == 0


def test_run_all_unknown_platform_raises():
    with pytest.raises(KeyError):
        run_all(["nonexistent"])


# ═══════════════════════════════════════════════════════════════
# Failure collection and consecutive counting
# ═══════════════════════════════════════════════════════════════


def test_record_failure_increments():
    assert _record_failure("test") == 1
    assert _record_failure("test") == 2
    assert _record_failure("test") == 3


def test_clear_failure_counter_resets():
    _record_failure("test")
    _record_failure("test")
    _clear_failure_counter("test")
    assert _record_failure("test") == 1


def test_consecutive_failures_triggers_alert():
    def _failing():
        raise RuntimeError("fail")

    cfg = CanaryConfig("alert-test", "alert-test", 10, 5.0, 2, _failing)

    from scripts import canary_runner as cr

    with patch.dict(cr.PLATFORM_MAP, {"alert-test": cfg}):
        # First failure — no alert
        report = run_all(["alert-test"])
        assert report.failed == 1
        assert len(report.alerts) == 0

        # Second failure — exceeds max_failures=2, alert fires
        report = run_all(["alert-test"])
        assert report.failed == 1
        assert len(report.alerts) == 1
        assert "alert-test" in report.alerts[0]
        assert "Canary Alert" in report.alerts[0]


def test_consecutive_reset_on_success():
    def _failing():
        raise RuntimeError("fail")

    cfg_fail = CanaryConfig("reset-test", "reset-test", 10, 5.0, 3, _failing)
    cfg_ok = CanaryConfig("reset-test", "reset-test", 10, 5.0, 3, lambda: {"ok": True})

    from scripts import canary_runner as cr

    with patch.dict(cr.PLATFORM_MAP, {"reset-test": cfg_fail}):
        # Two failures
        run_all(["reset-test"])
        run_all(["reset-test"])
        assert _consecutive_failures.get("reset-test", 0) == 2

    # Now success — counter should reset
    with patch.dict(cr.PLATFORM_MAP, {"reset-test": cfg_ok}):
        report = run_all(["reset-test"])
    assert report.passed == 1
    assert _consecutive_failures.get("reset-test", -1) == 0


def test_failure_counter_persists_across_process_runs(tmp_path):
    """A fresh invocation continues from the previously saved count."""
    state_file = tmp_path / "canary-state.json"

    def _failing():
        raise RuntimeError("fail")

    cfg = CanaryConfig("persist-test", "persist-test", 10, 5.0, 2, _failing)

    from scripts import canary_runner as cr

    with patch.dict(cr.PLATFORM_MAP, {"persist-test": cfg}):
        first = run_all(["persist-test"], state_file=state_file)
        assert first.alerts == []

        # Simulate the next CI process having no in-memory state.
        _consecutive_failures.clear()
        second = run_all(["persist-test"], state_file=state_file)

    assert len(second.alerts) == 1
    assert json.loads(state_file.read_text(encoding="utf-8"))["persist-test"] == 2


# ═══════════════════════════════════════════════════════════════
# Sanitisation
# ═══════════════════════════════════════════════════════════════


def test_sanitise_redacts_cookies():
    data = {"cookie": "secret_session=abc123", "user": "alice"}
    result = sanitise(data)
    assert result["cookie"] == "[REDACTED]"
    assert result["user"] == "alice"


def test_sanitise_redacts_tokens():
    data = {"access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.xxx"}
    result = sanitise(data)
    assert result["access_token"] == "[REDACTED]"


def test_sanitise_redacts_authorization():
    data = {"Authorization": "Bearer xyz", "Content-Type": "application/json"}
    result = sanitise(data)
    assert result["Authorization"] == "[REDACTED]"
    assert result["Content-Type"] == "application/json"


def test_sanitise_truncates_long_values():
    data = {"body": "a" * 500}
    result = sanitise(data)
    assert len(result["body"]) <= 215  # 200 chars + truncation suffix
    assert "...[TRUNCATED]" in result["body"]


def test_sanitise_preserves_short_values():
    data = {"status": "ok", "count": 42}
    result = sanitise(data)
    assert result["status"] == "ok"
    assert result["count"] == 42


def test_sanitise_handles_empty():
    assert sanitise({}) == {}


def test_sanitise_redacts_with_underscore_keys():
    data = {"x_csrf_token": "abc123"}
    result = sanitise(data)
    assert result["x_csrf_token"] == "[REDACTED]"


def test_sanitise_redacts_set_cookie():
    data = {"set_cookie": "sid=abc; HttpOnly"}
    result = sanitise(data)
    assert result["set_cookie"] == "[REDACTED]"


# ═══════════════════════════════════════════════════════════════
# build_diagnostics
# ═══════════════════════════════════════════════════════════════


def test_build_diagnostics_sanitises_sensitive_fields():
    result = CanaryResult(
        platform="test",
        ok=False,
        duration_ms=123.4,
        error_type="ValueError",
        error_message="bad cookie=abc",
        status_code=403,
        timestamp="2025-01-01T00:00:00+00:00",
    )
    cfg = _cfg()
    diag = build_diagnostics(result, cfg, "traceback line 1\ntraceback line 2")
    assert diag["ok"] is False
    assert diag["platform"] == "test"
    assert diag["error_type"] == "ValueError"
    # sanitise redacts keys, not values — error_message preserved
    assert diag["error_message"] == "bad cookie=abc"
    assert diag["status_code"] == 403
    assert "traceback" in diag["trace_snippet"]


def test_build_diagnostics_redacts_key_named_cookie():
    """A diagnostic with a 'cookie' key gets its value redacted."""
    raw = {
        "platform": "test",
        "cookie": "secret=xyz",
        "error_type": "AuthError",
    }
    out = sanitise(raw)
    assert out["cookie"] == "[REDACTED]"
    assert out["platform"] == "test"
    assert out["error_type"] == "AuthError"


def test_build_diagnostics_no_sensitive_data_preserved():
    result = CanaryResult(
        platform="test",
        ok=False,
        duration_ms=50.0,
        error_type="Timeout",
        error_message="Connection timed out",
    )
    cfg = _cfg()
    diag = build_diagnostics(result, cfg)
    assert diag["error_message"] == "Connection timed out"
    assert diag["error_type"] == "Timeout"


# ═══════════════════════════════════════════════════════════════
# Issue template
# ═══════════════════════════════════════════════════════════════


def test_format_issue_template_contains_platform_info():
    cfg = _cfg("weibo", max_failures=3)
    diag = {
        "timestamp": "2025-01-01T00:00:00+00:00",
        "error_type": "Timeout",
        "error_message": "request timed out",
        "status_code": None,
        "duration_ms": 5000.0,
    }
    template = format_issue_template(cfg, 3, diag)
    assert "Canary Alert" in template
    assert "weibo" in template
    assert "3/3" in template
    assert "Timeout" in template
    assert "5000" in template


def test_format_issue_template_includes_json_block():
    cfg = _cfg()
    diag = {"timestamp": "x", "error_type": "E", "error_message": "m", "duration_ms": 1.0}
    template = format_issue_template(cfg, 2, diag)
    assert "```json" in template
    assert "```" in template


# ═══════════════════════════════════════════════════════════════
# Concurrency isolation — independent failure counters
# ═══════════════════════════════════════════════════════════════


def test_platforms_independent_failure_counters():
    _record_failure("taobao")
    _record_failure("taobao")
    _record_failure("jd")

    assert _consecutive_failures["taobao"] == 2
    assert _consecutive_failures["jd"] == 1


def test_platform_failure_does_not_affect_others():
    def _taobao_fail():
        raise RuntimeError("taobao down")

    cfg_taobao = CanaryConfig("taobao", "taobao", 10, 5.0, 10, _taobao_fail)

    from scripts import canary_runner as cr

    with patch.dict(cr.PLATFORM_MAP, {"taobao": cfg_taobao}):
        report = run_all(["taobao"])
    assert report.failed == 1

    # jd counter should not be affected
    jd_count = _consecutive_failures.get("jd", 0)
    assert jd_count == 0  # jd never ran, so no counter entry


# ═══════════════════════════════════════════════════════════════
# Timeout / perf boundary
# ═══════════════════════════════════════════════════════════════


def test_run_canary_measures_duration():
    cfg = _cfg()
    result = run_canary(cfg)
    assert result.duration_ms >= 0
    # Should be very fast for a mock query
    assert result.duration_ms < 1000  # well under 1 second


# ═══════════════════════════════════════════════════════════════
# CLI — smoke tests for main()
# ═══════════════════════════════════════════════════════════════


def test_main_all_default(capsys, tmp_path):
    from scripts.canary_runner import main as canary_main

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("sys.argv", ["canary_runner.py", "--all"]):
            rc = canary_main()
            captured = capsys.readouterr()
            assert "8 passed" in captured.out or f"{8}" in captured.out
            assert rc == 0


def test_main_json_output(capsys, tmp_path):
    from scripts.canary_runner import main as canary_main

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("sys.argv", ["canary_runner.py", "--all", "--json"]):
            rc = canary_main()
            captured = capsys.readouterr()
            data = json.loads(captured.out)
            assert data["total"] == 8
            assert data["passed"] == 8
            assert data["failed"] == 0
            assert rc == 0


def test_main_single_platform(capsys, tmp_path):
    from scripts.canary_runner import main as canary_main

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch("sys.argv", ["canary_runner.py", "--platform", "zhihu"]):
            rc = canary_main()
            captured = capsys.readouterr()
            assert "zhihu" in captured.out
            assert rc == 0


def test_main_failure_exit_code(capsys, tmp_path):
    from scripts.canary_runner import main as canary_main

    def _fail():
        raise RuntimeError("boom")

    cfg_fail = CanaryConfig("bad", "bad", 10, 5.0, 5, _fail)

    from scripts import canary_runner as cr

    with patch("pathlib.Path.home", return_value=tmp_path):
        with patch.dict(cr.PLATFORM_MAP, {"bad": cfg_fail}):
            with patch("sys.argv", ["canary_runner.py", "--platform", "bad"]):
                rc = canary_main()
                assert rc == 1
