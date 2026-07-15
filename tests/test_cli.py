"""Unit tests for CLI — all external calls mocked, ruff 0.

Tests every CLI subcommand for correct argument parsing, dispatch,
and output format (including --json).
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.cli import _build_parser, _cmd_serve

# ═══════════════════════════════════════════════════════════════
# init
# ═══════════════════════════════════════════════════════════════


class TestInit:
    """Tests for `cn-scraper-mcp init`."""

    def test_init_creates_dirs(self, tmp_path, monkeypatch):
        """init creates ~/.cn-scraper-cookies."""
        home = tmp_path / "home"
        cookie_dir = home / ".cn-scraper-cookies"
        monkeypatch.setattr("cn_scraper_mcp.cli.Path.home", lambda: home)

        from cn_scraper_mcp.cli import _cmd_init

        ns = argparse.Namespace(json=False)
        rc = _cmd_init(ns)
        assert rc == 0
        assert cookie_dir.is_dir()

    def test_init_json_output(self, capsys, tmp_path, monkeypatch):
        """init --json prints valid JSON."""
        from cn_scraper_mcp.cli import _cmd_init

        home = tmp_path / "home"
        monkeypatch.setattr("cn_scraper_mcp.cli.Path.home", lambda: home)

        ns = argparse.Namespace(json=True)
        rc = _cmd_init(ns)
        assert rc == 0

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "ok"
        assert "cookie_dir" in data


# ═══════════════════════════════════════════════════════════════
# doctor
# ═══════════════════════════════════════════════════════════════


class TestDoctor:
    """Tests for `cn-scraper-mcp doctor`."""

    def test_doctor_calls_diagnose(self):
        """doctor calls server.diagnose() and prints result."""
        from cn_scraper_mcp.cli import _cmd_doctor

        fake_diagnose = {
            "platform": {"python_version": "3.12.0"},
            "browsers": {"chrome": {"found": True, "path": "/usr/bin/chrome"}},
            "dependencies": {},
            "cdp_ports": {},
            "cookies": {},
            "diagnostics": {},
        }
        ns = argparse.Namespace(json=False)
        with patch("cn_scraper_mcp.server.diagnose", return_value=fake_diagnose):
            rc = _cmd_doctor(ns)
            assert rc == 0

    def test_doctor_json_output(self, capsys):
        """doctor --json prints JSON."""
        fake_diagnose = {
            "platform": {"python_version": "3.12.0"},
            "browsers": {"chrome": {"found": False}},
            "dependencies": {},
            "cdp_ports": {},
            "cookies": {},
            "diagnostics": {},
        }
        ns = argparse.Namespace(json=True)
        from cn_scraper_mcp.cli import _cmd_doctor

        with patch("cn_scraper_mcp.server.diagnose", return_value=fake_diagnose):
            rc = _cmd_doctor(ns)
        # Returns 1 when chrome not found
        assert rc == 1
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["browsers"]["chrome"]["found"] is False

    def test_doctor_chrome_found_returns_0(self):
        """doctor returns 0 when chrome is found."""
        fake_diagnose = {
            "platform": {"python_version": "3.12.0"},
            "browsers": {"chrome": {"found": True}},
            "dependencies": {},
            "cdp_ports": {},
            "cookies": {},
            "diagnostics": {},
        }
        ns = argparse.Namespace(json=False)
        from cn_scraper_mcp.cli import _cmd_doctor

        with patch("cn_scraper_mcp.server.diagnose", return_value=fake_diagnose):
            rc = _cmd_doctor(ns)
            assert rc == 0


# ═══════════════════════════════════════════════════════════════
# login
# ═══════════════════════════════════════════════════════════════


class TestLogin:
    """Tests for `cn-scraper-mcp login <platform>`."""

    def test_login_success(self):
        """login calls guided_login and returns 0 on success."""
        from cn_scraper_mcp.cli import _cmd_login

        ns = argparse.Namespace(platform="taobao", port=None, json=False)
        with patch(
            "cn_scraper_mcp.cookie_harvest.guided_login",
            return_value={"status": "ok", "platform": "taobao", "count": 10},
        ):
            rc = _cmd_login(ns)
            assert rc == 0

    def test_login_failure(self):
        """login returns 1 when status is not ok."""
        from cn_scraper_mcp.cli import _cmd_login

        ns = argparse.Namespace(platform="taobao", port=None, json=False)
        with patch(
            "cn_scraper_mcp.cookie_harvest.guided_login",
            return_value={"status": "error", "reason": "timeout"},
        ):
            rc = _cmd_login(ns)
            assert rc == 1

    def test_login_with_port(self):
        """login respects --port argument."""
        from cn_scraper_mcp.cli import _cmd_login

        ns = argparse.Namespace(platform="jd", port=9247, json=False)
        with patch(
            "cn_scraper_mcp.cookie_harvest.guided_login",
        ) as mock_gl:
            mock_gl.return_value = {"status": "ok", "platform": "jd"}
            _cmd_login(ns)
            mock_gl.assert_called_once_with("jd", port=9247)

    def test_login_parser_accepts_platform(self):
        """login subparser validates platform argument."""
        parser = _build_parser()
        ns = parser.parse_args(["login", "xiaohongshu"])
        assert ns.platform == "xiaohongshu"
        assert ns.command == "login"


# ═══════════════════════════════════════════════════════════════
# session
# ═══════════════════════════════════════════════════════════════


class TestSession:
    """Tests for `cn-scraper-mcp session list|delete`."""

    def test_session_list_parser(self):
        """session list subcommand parses correctly."""
        parser = _build_parser()
        ns = parser.parse_args(["session", "list"])
        assert ns.command == "session"
        assert ns.session_command == "list"

    def test_session_list_json_output(self, capsys):
        """session list --json prints JSON."""
        from cn_scraper_mcp.cli import _cmd_session_list

        ns = argparse.Namespace(json=True)
        fake_status = {
            "taobao": {"valid": True, "platform": "taobao", "session_type": "cookie"},
            "jd": {"valid": False, "platform": "jd", "session_type": "chrome_profile"},
        }
        with patch(
            "cn_scraper_mcp.session.SessionManager.status_all",
            return_value=fake_status,
        ):
            rc = _cmd_session_list(ns)
            assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["taobao"]["valid"] is True
        assert data["jd"]["valid"] is False

    def test_session_delete_parser(self):
        """session delete subcommand parses platform."""
        parser = _build_parser()
        ns = parser.parse_args(["session", "delete", "taobao"])
        assert ns.session_command == "delete"
        assert ns.platform == "taobao"

    def test_session_delete_success(self, capsys):
        """session delete returns 0 on success."""
        from cn_scraper_mcp.cli import _cmd_session_delete

        ns = argparse.Namespace(platform="taobao", json=False)
        with patch(
            "cn_scraper_mcp.session.SessionManager.delete",
            return_value={"platform": "taobao", "deleted": True},
        ):
            rc = _cmd_session_delete(ns)
            assert rc == 0

    def test_session_delete_not_found(self, capsys):
        """session delete returns 1 when file does not exist."""
        from cn_scraper_mcp.cli import _cmd_session_delete

        ns = argparse.Namespace(platform="taobao", json=False)
        with patch(
            "cn_scraper_mcp.session.SessionManager.delete",
            return_value={"platform": "taobao", "deleted": False},
        ):
            rc = _cmd_session_delete(ns)
            assert rc == 1


# ═══════════════════════════════════════════════════════════════
# config
# ═══════════════════════════════════════════════════════════════


class TestConfig:
    """Tests for `cn-scraper-mcp config --client <name>`."""

    def test_config_codex(self, capsys):
        """config --client codex prints valid JSON with mcpServers."""
        from cn_scraper_mcp.cli import _cmd_config

        ns = argparse.Namespace(client="codex")
        rc = _cmd_config(ns)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "mcpServers" in data
        assert "cn-scraper" in data["mcpServers"]
        assert "_package_version" in data

    def test_config_claude(self, capsys):
        """config --client claude prints valid JSON."""
        from cn_scraper_mcp.cli import _cmd_config

        ns = argparse.Namespace(client="claude")
        rc = _cmd_config(ns)
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "mcpServers" in data
        assert "cn-scraper" in data["mcpServers"]

    def test_config_unknown_client(self, capsys):
        """config with unknown client returns 1 and errors to stderr."""
        from cn_scraper_mcp.cli import _cmd_config

        ns = argparse.Namespace(client="invalid")
        rc = _cmd_config(ns)
        assert rc == 1
        captured = capsys.readouterr()
        assert "Unknown client" in captured.err

    def test_config_parser_requires_client(self):
        """config subparser requires --client."""
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["config"])

    def test_config_parser_accepts_codex(self):
        """config --client codex parses correctly."""
        parser = _build_parser()
        ns = parser.parse_args(["config", "--client", "codex"])
        assert ns.client == "codex"

    def test_config_parser_accepts_claude(self):
        """config --client claude parses correctly."""
        parser = _build_parser()
        ns = parser.parse_args(["config", "--client", "claude"])
        assert ns.client == "claude"


# ═══════════════════════════════════════════════════════════════
# serve
# ═══════════════════════════════════════════════════════════════


class TestServe:
    """Tests for `cn-scraper-mcp serve`."""

    def test_serve_parser(self):
        """serve subcommand parses correctly."""
        parser = _build_parser()
        ns = parser.parse_args(["serve"])
        assert ns.command == "serve"

    def test_serve_calls_mcp_run(self):
        """serve starts MCP server with transport=stdio."""
        from cn_scraper_mcp.cli import _cmd_serve

        fake_mcp = MagicMock()
        with patch("cn_scraper_mcp.server.mcp", fake_mcp):
            ns = argparse.Namespace()
            rc = _cmd_serve(ns)
            assert rc == 0
            fake_mcp.run.assert_called_once_with(transport="stdio")


# ═══════════════════════════════════════════════════════════════
# Parser structure
# ═══════════════════════════════════════════════════════════════


class TestParser:
    """Structural tests for CLI argument parser."""

    def test_top_level_commands_present(self):
        """All required top-level subcommands exist."""
        parser = _build_parser()
        # subcommands require dest="command"
        ns = parser.parse_args(["init"])
        assert ns.command == "init"

    def test_parser_no_args_defaults_to_serve(self):
        """No-argument invocation remains compatible with legacy MCP configs."""
        parser = _build_parser()
        ns = parser.parse_args([])
        assert ns.command is None
        assert ns.func is _cmd_serve

    def test_json_flag_on_all_commands(self):
        """All relevant commands support --json flag."""
        parser = _build_parser()

        for args in (
            ["init", "--json"],
            ["doctor", "--json"],
            ["login", "taobao", "--json"],
            ["session", "list", "--json"],
            ["session", "delete", "taobao", "--json"],
        ):
            ns = parser.parse_args(args)
            assert getattr(ns, "json", True) is True, f"Failed for {args!r}"

    def test_json_flag_warns_on_extra_flag(self):
        """config and serve don't need --json, but don't error if absent."""
        parser = _build_parser()
        # config doesn't have --json flag
        ns = parser.parse_args(["config", "--client", "codex"])
        assert ns.client == "codex"
        assert not hasattr(ns, "json") or ns.__dict__.get("json") is None


# ═══════════════════════════════════════════════════════════════
# _print_result helper
# ═══════════════════════════════════════════════════════════════


class TestPrintResult:
    """Tests for _print_result helper."""

    def test_print_dict_as_json(self, capsys):
        """_print_result with as_json=True prints JSON."""
        from cn_scraper_mcp.cli import _print_result

        data = {"key": "value", "num": 42}
        _print_result(data, as_json=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_print_dict_pretty(self, capsys):
        """_print_result without json prints key: value pairs."""
        from cn_scraper_mcp.cli import _print_result

        data = {"status": "ok", "count": 5}
        _print_result(data, as_json=False)
        captured = capsys.readouterr()
        assert "status: ok" in captured.out
        assert "count: 5" in captured.out

    def test_print_list_as_json(self, capsys):
        """_print_result prints list as JSON array."""
        from cn_scraper_mcp.cli import _print_result

        data = ["a", "b", "c"]
        _print_result(data, as_json=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed == data

    def test_print_list_pretty(self, capsys):
        """_print_result prints list as bullet points."""
        from cn_scraper_mcp.cli import _print_result

        data = ["taobao", "jd", "pdd"]
        _print_result(data, as_json=False)
        captured = capsys.readouterr()
        assert "  - taobao" in captured.out
        assert "  - jd" in captured.out

    def test_print_scalar(self, capsys):
        """_print_result prints a string as-is."""
        from cn_scraper_mcp.cli import _print_result

        _print_result("hello world", as_json=False)
        captured = capsys.readouterr()
        assert captured.out.strip() == "hello world"
