"""Integration smoke-test: launch cn-scraper-mcp and call tools via MCP protocol.

Run: python scripts/mcp_smoke_test.py

Thread-based I/O — works on all platforms including Windows.
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time as _time
from pathlib import Path

# Resolve project root relative to this script
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _reader_thread(stream, q):
    """Read lines from *stream* and put them into *q*.  Stops on EOF."""
    try:
        for line in iter(stream.readline, ""):
            if q is not None:
                q.put(line)
    except Exception:
        pass


def send_jsonrpc(proc, req: dict, q, timeout: float = 30) -> dict:
    """Send a JSON-RPC request via stdin.  Read response from the
    reader thread's queue, bounded by *timeout*."""
    line = json.dumps(req, ensure_ascii=False) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if proc.poll() is not None:
            return {"error": f"process exited rc={proc.returncode}", "id": req.get("id")}
        try:
            raw = q.get(timeout=0.5).strip()
        except queue.Empty:
            continue
        if not raw:
            continue
        try:
            resp = json.loads(raw)
            if resp.get("id") == req.get("id"):
                return resp
        except json.JSONDecodeError:
            continue
    return {"error": "timeout", "id": req.get("id")}


def call_tool(proc, name: str, args: dict, msg_id: int, q) -> dict:
    """Call an MCP tool and extract the result content as a dict."""
    req = {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    resp = send_jsonrpc(proc, req, q)
    if "error" in resp:
        return resp
    content = resp.get("result", {}).get("content", [])
    if content and len(content) > 0:
        text = content[0].get("text", "")
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
    return resp.get("result", {})


def test_list_tools(proc, msg_id: int, q) -> bool:
    """Verify tools/list returns all expected tools."""
    req = {"jsonrpc": "2.0", "id": msg_id, "method": "tools/list", "params": {}}
    resp = send_jsonrpc(proc, req, q)
    tools = resp.get("result", {}).get("tools", [])
    names = sorted([t["name"] for t in tools])
    expected = sorted([
        "taobao_search", "jd_search", "pdd_search", "pdd_product_detail",
        "xiaohongshu_search", "xiaohongshu_note", "xiaohongshu_comments",
        "zhihu_search", "zhihu_hot_list", "zhihu_comments",
        "weibo_search", "weibo_hot_list", "weibo_user_timeline", "weibo_comments",
        "douyin_search", "douyin_hot_list",
        "zsxq_topics",
        "check_cookies", "verify_login", "diagnose",
        "harvest_cookies", "guided_login",
    ])
    print(f"  tools/list: {len(names)} tools")
    assert names == expected, (
        f"MCP tool contract drifted; missing={sorted(set(expected) - set(names))}, "
        f"unexpected={sorted(set(names) - set(expected))}"
    )
    print(f"  All {len(expected)} expected tools present")
    return True


def main():
    print("=" * 60)
    print("cn-scraper-mcp Integration Smoke Test")
    print(f"  Project root: {PROJECT_ROOT}")
    print("=" * 60)

    # Isolate the subprocess from the developer's real cookie files and profiles.
    temp_home = tempfile.TemporaryDirectory(prefix="cn-scraper-smoke-")
    child_env = os.environ.copy()
    child_env.update({
        "HOME": temp_home.name,
        "USERPROFILE": temp_home.name,
        "TAOBAO_COOKIES_FILE": str(Path(temp_home.name) / "taobao.json"),
        "XHS_COOKIES_FILE": str(Path(temp_home.name) / "xiaohongshu.json"),
        "ZHIHU_COOKIES_FILE": str(Path(temp_home.name) / "zhihu.json"),
        "ZSXQ_COOKIES_FILE": str(Path(temp_home.name) / "zsxq.json"),
        "WEIBO_COOKIES_FILE": str(Path(temp_home.name) / "weibo.json"),
        "DOUYIN_COOKIES_FILE": str(Path(temp_home.name) / "douyin.json"),
        "PDD_COOKIES_FILE": str(Path(temp_home.name) / "pdd.json"),
    })

    proc = subprocess.Popen(
        [sys.executable, "-m", "cn_scraper_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=str(PROJECT_ROOT),
        env=child_env,
    )

    # Reader threads prevent pipe buffer deadlocks
    out_queue = queue.Queue()
    t_out = threading.Thread(target=_reader_thread, args=(proc.stdout, out_queue), daemon=True)
    t_out.start()
    t_err = threading.Thread(target=_reader_thread, args=(proc.stderr, None), daemon=True)
    t_err.start()

    failures = []
    msg_id = 1
    tests_run = 0

    def run_test(name: str, fn):
        nonlocal msg_id, tests_run
        tests_run += 1
        print(f"\n--- {name} ---")
        try:
            fn()
            print("  PASS")
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failures.append(f"{name}: {e}")
        except Exception as e:
            print(f"  FAIL: {e}")
            failures.append(f"{name}: {e} (EXCEPTION)")

    try:
        # Initialize
        resp = send_jsonrpc(proc, {
            "jsonrpc": "2.0", "id": msg_id, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "1.0"},
            },
        }, out_queue)
        assert "error" not in resp, f"initialize failed: {resp.get('error')}"
        server_info = resp.get("result", {}).get("serverInfo", {})
        server_name = server_info.get("name", "?")
        server_version = server_info.get("version", "?")
        from cn_scraper_mcp import __version__
        assert server_version == __version__, (
            f"MCP version {server_version!r} != package version {__version__!r}"
        )
        print(f"\n  Server: {server_name} {server_version}")
        msg_id += 1

        # initialized notification
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # tools/list
        def _tools():
            test_list_tools(proc, msg_id, out_queue)
        run_test("tools/list", _tools)
        msg_id += 1

        # input validation — empty keyword
        def _empty_keyword():
            result = call_tool(proc, "taobao_search", {"keyword": "", "limit": 10}, msg_id, out_queue)
            assert isinstance(result, dict), f"expected dict, got {type(result)}"
            err = result.get("error", {})
            has_err = bool(err)
            assert has_err, f"No error in validation response: {result}"
            msg = err.get("message", "")
            assert "empty" in msg.lower() or "not be empty" in msg.lower(), f"Unexpected error: {msg}"
        run_test("validation (empty keyword)", _empty_keyword)
        msg_id += 1

        # diagnose
        def _diagnose():
            result = call_tool(proc, "diagnose", {}, msg_id, out_queue)
            assert "platform" in result, "diagnose missing platform"
            assert "dependencies" in result, "diagnose missing dependencies"
        run_test("diagnose", _diagnose)
        msg_id += 1

        # check_cookies
        def _cookies():
            result = call_tool(proc, "check_cookies", {}, msg_id, out_queue)
            assert isinstance(result, dict), f"expected dict, got {type(result)}"
            assert "taobao" in result, "check_cookies missing taobao"
        run_test("check_cookies", _cookies)
        msg_id += 1

        # tools requiring cookies — each with its correct parameter set
        cookie_tools = [
            ("zhihu_search", {"keyword": "test", "limit": 3}),
            ("zhihu_comments", {"answer_id": "1", "limit": 3}),
            ("weibo_search", {"keyword": "test", "limit": 3}),
            ("weibo_comments", {"mid": "1", "limit": 3}),
            ("zsxq_topics", {"group_id": "28888555451", "count": 3}),
            ("douyin_hot_list", {}),
        ]
        for tool, params in cookie_tools:
            def _make_test(tool_name, tool_params):
                def _inner():
                    result = call_tool(proc, tool_name, tool_params, msg_id, out_queue)
                    assert isinstance(result, dict), f"expected dict, got {type(result)}"
                    assert "error" in result, f"expected missing-cookie error: {result}"
                return _inner
            run_test(f"{tool} (needs cookies)", _make_test(tool, params))
            msg_id += 1

    except Exception as e:
        print(f"\n\nUNHANDLED: {e}", file=sys.stderr)
        failures.append(f"unhandled: {e}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        temp_home.cleanup()

    print(f"\n{'=' * 60}")
    if failures:
        print(f"FAILED ({len(failures)}/{tests_run}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print(f"ALL {tests_run} SMOKE TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
