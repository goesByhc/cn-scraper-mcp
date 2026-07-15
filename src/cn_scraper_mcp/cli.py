"""CLI for cn-scraper-mcp — init, doctor, login, session, config, serve.

Commands:
    cn-scraper-mcp init                        Initialize ~/.cn-scraper-cookies/
    cn-scraper-mcp doctor                      Diagnose environment
    cn-scraper-mcp login <platform>            Launch guided login
    cn-scraper-mcp session list                List all platform login status
    cn-scraper-mcp session delete <platform>   Delete platform login state
    cn-scraper-mcp config --client codex      Generate Codex/Claude MCP config
    cn-scraper-mcp config --client claude
    cn-scraper-mcp serve                       Start MCP server (stdio)

All commands support --json for machine-readable output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_result(data: object, as_json: bool = False) -> None:
    """Print result to stdout — either as JSON or pretty-printed dict."""
    if as_json:
        if isinstance(data, (dict, list, tuple)):
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(str(data))
    else:
        if isinstance(data, dict):
            for key, value in data.items():
                print(f"{key}: {value}")
        elif isinstance(data, (list, tuple)):
            for item in data:
                print(f"  - {item}")
        else:
            print(data)


# ═══════════════════════════════════════════════════════════════
# Subcommand: init
# ═══════════════════════════════════════════════════════════════


def _cmd_init(args: argparse.Namespace) -> int:
    """Initialize ~/.cn-scraper-cookies/ directory and config."""
    cookie_dir = Path.home() / ".cn-scraper-cookies"

    cookie_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "status": "ok",
        "cookie_dir": str(cookie_dir),
    }
    _print_result(result, as_json=args.json)
    return 0


# ═══════════════════════════════════════════════════════════════
# Subcommand: doctor
# ═══════════════════════════════════════════════════════════════


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run environment diagnostics."""
    from cn_scraper_mcp.server import diagnose

    result = diagnose()
    _print_result(result, as_json=args.json)

    # Check if critical issues exist
    chrome_ok = result.get("browsers", {}).get("chrome", {}).get("found", False)
    if not chrome_ok:
        return 1
    return 0


# ═══════════════════════════════════════════════════════════════
# Subcommand: login
# ═══════════════════════════════════════════════════════════════


def _cmd_login(args: argparse.Namespace) -> int:
    """Launch guided login for a platform."""
    from cn_scraper_mcp.cookie_harvest import guided_login

    port = args.port if hasattr(args, "port") and args.port else None
    result = guided_login(args.platform, port=port)
    _print_result(result, as_json=args.json)
    return 0 if result.get("status") == "ok" else 1


# ═══════════════════════════════════════════════════════════════
# Subcommand: session
# ═══════════════════════════════════════════════════════════════


def _cmd_session_list(args: argparse.Namespace) -> int:
    """List all platform login status."""
    from cn_scraper_mcp.session import SessionManager

    mgr = SessionManager()
    result = mgr.status_all()
    _print_result(result, as_json=args.json)
    return 0


def _cmd_session_delete(args: argparse.Namespace) -> int:
    """Delete a platform's login state."""
    from cn_scraper_mcp.session import SessionManager

    mgr = SessionManager()
    result = mgr.delete(args.platform)
    _print_result(result, as_json=args.json)
    return 0 if result.get("deleted") else 1


# ═══════════════════════════════════════════════════════════════
# Subcommand: config
# ═══════════════════════════════════════════════════════════════


def _cmd_config(args: argparse.Namespace) -> int:
    """Generate MCP client configuration JSON."""
    from cn_scraper_mcp import __version__

    client = args.client

    # Determine the command based on how the package is installed
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "-m", "cn_scraper_mcp.server"]
    else:
        cmd = ["cn-scraper-mcp", "serve"]

    configs: dict[str, dict] = {
        "codex": {
            "mcpServers": {
                "cn-scraper": {
                    "command": cmd[0],
                    "args": cmd[1:] if len(cmd) > 1 else [],
                    "env": {
                        "CN_SCRAPER_LOG_LEVEL": "WARNING",
                    },
                },
            },
        },
        "claude": {
            "mcpServers": {
                "cn-scraper": {
                    "command": cmd[0],
                    "args": cmd[1:] if len(cmd) > 1 else [],
                    "env": {
                        "CN_SCRAPER_LOG_LEVEL": "WARNING",
                    },
                },
            },
        },
    }

    if client not in configs:
        print(f"Unknown client: {client}. Supported: codex, claude", file=sys.stderr)
        return 1

    result = configs[client]
    result["_package_version"] = __version__

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ═══════════════════════════════════════════════════════════════
# Subcommand: serve
# ═══════════════════════════════════════════════════════════════


def _cmd_serve(_args: argparse.Namespace) -> int:
    """Start MCP server on stdio."""
    # _args is unused — serve command takes no arguments
    from cn_scraper_mcp.server import mcp

    mcp.run(transport="stdio")
    return 0

# ═══════════════════════════════════════════════════════════════
# Argparse builder
# ═══════════════════════════════════════════════════════════════


def _build_parser() -> argparse.ArgumentParser:
    """Build the complete CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="cn-scraper-mcp",
        description="CN Scraper MCP — Chinese web scraping tools CLI",
    )

    subparsers = parser.add_subparsers(dest="command", title="commands")
    # Backward compatibility: before the CLI gained subcommands, invoking the
    # console script without arguments started the stdio MCP server.  Existing
    # Codex/Claude configurations rely on that behaviour.
    parser.set_defaults(func=_cmd_serve)

    # ── init ────────────────────────────────────────────────
    p_init = subparsers.add_parser("init", help="Initialize ~/.cn-scraper-cookies/ directory")
    p_init.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_init.set_defaults(func=_cmd_init)

    # ── doctor ──────────────────────────────────────────────
    p_doctor = subparsers.add_parser("doctor", help="Diagnose environment (browser, deps, cookies)")
    p_doctor.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_doctor.set_defaults(func=_cmd_doctor)

    # ── login ───────────────────────────────────────────────
    p_login = subparsers.add_parser("login", help="Launch guided login for a platform")
    p_login.add_argument("platform", help="Platform name (e.g. taobao, jd, xiaohongshu)")
    p_login.add_argument("--port", type=int, default=None, help="CDP debug port (optional)")
    p_login.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_login.set_defaults(func=_cmd_login)

    # ── session ─────────────────────────────────────────────
    p_session = subparsers.add_parser("session", help="Session management")
    session_subs = p_session.add_subparsers(dest="session_command")
    session_subs.required = True

    p_session_list = session_subs.add_parser("list", help="List all platform login status")
    p_session_list.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    p_session_list.set_defaults(func=_cmd_session_list)

    p_session_delete = session_subs.add_parser("delete", help="Delete platform login state")
    p_session_delete.add_argument("platform", help="Platform name (e.g. taobao)")
    p_session_delete.add_argument(
        "--json", action="store_true", help="Machine-readable JSON output"
    )
    p_session_delete.set_defaults(func=_cmd_session_delete)

    # ── config ──────────────────────────────────────────────
    p_config = subparsers.add_parser("config", help="Generate MCP client configuration")
    p_config.add_argument(
        "--client",
        choices=["codex", "claude"],
        required=True,
        help="Target client (codex or claude)",
    )
    p_config.set_defaults(func=_cmd_config)

    # ── serve ───────────────────────────────────────────────
    p_serve = subparsers.add_parser("serve", help="Start MCP server on stdio")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════


def main() -> None:
    """Entry point for `cn-scraper-mcp` CLI command."""
    parser = _build_parser()
    args = parser.parse_args()

    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
