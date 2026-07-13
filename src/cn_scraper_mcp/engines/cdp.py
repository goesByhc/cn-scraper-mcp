"""Chrome DevTools Protocol (CDP) driver via raw websockets.

No Playwright, no Selenium — just stdlib urllib + websockets.
Used by JD and Pinduoduo engines to control a local Chrome instance.

Usage:
    with CDPClient(port=9222) as cdp:
        cdp.enable()                     # enable Page, Runtime, Network
        cdp.navigate("https://...")      # go to a URL
        result = cdp.evaluate(js_code)   # run JS in the page

── Port Strategy ──────────────────────────────────────────────

Chrome:
    Each engine gets its own debug port to avoid collisions:
    - JD: port 9247 (default, user-assignable via `port=` parameter)
    - Xiaohongshu: port 9251 (default, user-assignable)
    Pass a custom port on engine init to override.

Obscura:
    Fixed to port 9222 by the Obscura CLI (--port flag). This is a
    constraint of the Obscura binary — it does not support dynamic
    port assignment. Engines that prefer Obscura (XHS) use 9222 and
    must coordinate to avoid double-launching.

── Process Lifecycle ──────────────────────────────────────────

    launch_chrome() and launch_obscura() return the subprocess.Popen
    handle. Handles are tracked in the module-level _managed_processes
    dict so close_browser(port) can terminate ONLY processes we own.

    NEVER kill all Chrome/Obscura processes globally (no taskkill //F
    //IM chrome.exe). Use close_browser(port) which only terminates
    processes we launched.

    Port conflicts: if the target port is busy and not owned by us,
    launch_chrome/launch_obscura return an error string instead of
    stealing the user's running browser.
"""

import asyncio
import json
import os as _os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


class CDPError(Exception):
    """CDP protocol error."""
    pass


class CDPClient:
    """Control a Chrome instance via Chrome DevTools Protocol."""

    def __init__(self, port: int = 9222, timeout: float = 30):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        self.ws = None
        self._msg_id = 0
        self._connected = False

    # ── connection management ────────────────────────────

    def _get_json(self, path: str) -> Any:
        """GET a JSON endpoint on the CDP HTTP server."""
        u = f"{self.base}{path}"
        resp = urllib.request.urlopen(u, timeout=5)
        return json.loads(resp.read())

    def _find_page_target(self, url_hint: str | None = None):
        """Find a page target to connect to. Optionally filter by URL hint."""
        targets = self._get_json("/json")
        pages = [t for t in targets if t.get("type") == "page"]
        if url_hint:
            pages = [t for t in pages if url_hint in t.get("url", "")]
        if not pages:
            raise CDPError("No page target found. Is Chrome running with --remote-debugging-port?")
        return pages[0]["webSocketDebuggerUrl"]

    async def connect(self, url_hint: str | None = None):
        """Connect to a Chrome page target."""
        import websockets
        ws_url = self._find_page_target(url_hint)
        self.ws = await asyncio.wait_for(
            websockets.connect(ws_url, max_size=120_000_000),
            timeout=self.timeout,
        )
        self._connected = True

    async def close(self):
        """Close the websocket connection."""
        if self.ws:
            await self.ws.close()
            self._connected = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ── CDP commands ──────────────────────────────────────

    async def _send(self, method: str, params: dict | None = None) -> dict:
        """Send a CDP command and return the result."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        self._msg_id += 1
        mid = self._msg_id
        msg = {"id": mid, "method": method, "params": params or {}}
        await self.ws.send(json.dumps(msg))
        # wait for matching response
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=self.timeout)
            resp = json.loads(raw)
            if resp.get("id") == mid:
                if "error" in resp:
                    raise CDPError(f"CDP error: {resp['error']}")
                return resp.get("result", {})

    async def enable(self):
        """Enable core CDP domains (Page, Runtime, Network)."""
        await self._send("Page.enable")
        await self._send("Runtime.enable")
        await self._send("Network.enable")

    async def navigate(self, url: str, wait: float = 5):
        """Navigate to a URL and wait for it to load."""
        await self._send("Page.navigate", {"url": url})
        await asyncio.sleep(wait)

    async def evaluate(self, expression: str, return_by_value: bool = True) -> Any:
        """Evaluate JavaScript in the page and return the result."""
        result = await self._send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": return_by_value,
            "timeout": 8000,
        })
        sub = result.get("result", {})
        if sub.get("type") == "object" and "value" in sub:
            return sub["value"]
        if "exceptionDetails" in result:
            raise CDPError(f"JS exception: {result['exceptionDetails']}")
        return None

    def poll(self, expression: str, tries: int = 8, interval: float = 2) -> Any:
        """Poll a JS expression until it returns a non-trivial result.

        Synchronous wrapper around async evaluate — use when you don't need
        an existing event loop.
        """

        async def _poll():
            for _ in range(tries):
                await asyncio.sleep(interval)
                v = await self.evaluate(expression)
                if v:
                    return v
            return None

        return asyncio.run(_poll())


# ── Process tracking ─────────────────────────────────────────

_managed_processes: dict[int, subprocess.Popen] = {}
"""Processes we launched, keyed by debug port.

Only close_browser() may terminate these. NEVER use taskkill //F //IM
to kill all Chrome/Obscura processes — that nukes the user's browser.
"""


def _register_process(port: int, proc: subprocess.Popen) -> None:
    """Track a browser process we launched."""
    _managed_processes[port] = proc


def _unregister_process(port: int) -> None:
    """Remove a process from tracking (e.g. after termination)."""
    _managed_processes.pop(port, None)


def _is_our_port(port: int) -> bool:
    """Check if the given port was launched by us."""
    proc = _managed_processes.get(port)
    if proc is None:
        return False
    return proc.poll() is None  # still running


def _port_in_use(port: int) -> bool:
    """Check if any process is listening on the given CDP port."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return True
    except Exception:
        return False


def close_browser(port: int) -> bool:
    """Terminate ONLY the browser process we launched on this port.

    Does NOT touch the user's personal Chrome or other browsers.
    Only terminates processes registered via launch_chrome()/launch_obscura().

    Args:
        port: CDP debug port of the process to terminate.

    Returns:
        True if process was terminated, False if no process was found
        for this port or it was already dead.
    """
    proc = _managed_processes.pop(port, None)
    if proc is None:
        return False

    if proc.poll() is not None:
        # Already exited on its own
        return False

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        return True
    except Exception:
        return False


def close_all_browsers() -> int:
    """Terminate ALL browser processes we launched.

    Returns:
        Number of processes terminated.
    """
    count = 0
    for port in list(_managed_processes.keys()):
        if close_browser(port):
            count += 1
    return count


# ── Chrome process management ───────────────────────────────

def find_chrome() -> str | None:
    """Locate the Chrome/Chromium executable."""
    import glob
    patterns = [
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        str(Path.home() / ".agent-browser/browsers/chrome-*/chrome.exe"),
    ]
    for pat in patterns:
        found = sorted(glob.glob(pat))
        if found:
            return found[-1]
    return None


def is_chrome_running(port: int) -> bool:
    """Check if Chrome is listening on the given debug port."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2)
        return True
    except Exception:
        return False


def launch_chrome(
    port: int,
    profile_dir: str,
    url: str = "about:blank",
    headless: bool = False,
) -> subprocess.Popen | None:
    """Launch Chrome in remote-debugging mode.

    Args:
        port: Debug port for CDP
        profile_dir: Chrome user data directory (persistent login state)
        url: Initial URL to open
        headless: Run in headless mode (JD requires headful=False!)

    Returns:
        subprocess.Popen handle on success, or None if Chrome didn't
        become ready in time.

    Raises:
        FileNotFoundError: Chrome executable not found.
        RuntimeError: Port is in use by another (non-ours) process.
    """
    chrome = find_chrome()
    if not chrome:
        raise FileNotFoundError("Chrome not found. Install Chrome or set CHROME_PATH.")

    # ── Port conflict detection ──────────────────────────
    if _port_in_use(port):
        if _is_our_port(port):
            # Already launched by us — return existing handle
            return _managed_processes[port]
        raise RuntimeError(
            f"Port {port} is already in use by another process. "
            f"Use a different port or close the existing browser on that port."
        )

    # ── Profile lock handling ────────────────────────────
    lock = _os.path.join(profile_dir, "SingletonLock")
    if _os.path.exists(lock):
        try:
            _os.remove(lock)
        except (OSError, PermissionError) as e:
            raise RuntimeError(
                f"Cannot remove Chrome SingletonLock at {lock}: {e}. "
                f"The profile may be in use by another Chrome instance."
            )

    _os.makedirs(profile_dir, exist_ok=True)

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,1000",
    ]
    if headless:
        args.append("--headless=new")
    args.append(url)

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Track this process
    _register_process(port, proc)

    # Wait for Chrome to be ready
    for _ in range(15):
        time.sleep(1)
        if is_chrome_running(port):
            return proc

    # Didn't come up — the process may have crashed
    return None


# ── Obscura (lightweight headless browser for AI agents) ─────

def find_obscura() -> str | None:
    """Locate the Obscura executable (Rust headless browser)."""
    import glob
    patterns = [
        "C:/Program Files/Obscura/obscura.exe",
        str(Path.home() / ".agent-browser/browsers/obscura-*/obscura.exe"),
    ]
    for pat in patterns:
        found = sorted(glob.glob(pat))
        if found:
            return found[-1]
    return None


def launch_obscura(port: int = 9222, stealth: bool = True) -> subprocess.Popen | None:
    """Launch Obscura in CDP serve mode.

    Obscura is a lightweight (~30MB RAM) Rust headless browser with
    built-in anti-detection. Uses the same CDP protocol as Chrome.

    NOTE: Obscura CLI uses port 9222 by default (fixed by the binary).
    Other ports may work via --port but this is not guaranteed by Obscura.

    Args:
        port: CDP debug port (default 9222 — Obscura's built-in)
        stealth: Enable stealth mode (consistent fingerprint, TLS impersonation)

    Returns:
        subprocess.Popen handle on success, or None if Obscura didn't
        become ready in time.

    Raises:
        FileNotFoundError: Obscura executable not found.
        RuntimeError: Port is in use by another (non-ours) process.
    """
    obscura = find_obscura()
    if not obscura:
        raise FileNotFoundError(
            "Obscura not found. Download from https://github.com/h4ckf0r0day/obscura/releases\n"
            "Place in ~/.agent-browser/browsers/obscura-<version>/obscura.exe"
        )

    # ── Port conflict detection ──────────────────────────
    if _port_in_use(port):
        if _is_our_port(port):
            return _managed_processes[port]
        raise RuntimeError(
            f"Port {port} is already in use by another process. "
            f"Use a different port or close the existing browser on that port."
        )

    args = [obscura, "--port", str(port)]
    if stealth:
        args.append("--stealth")
    args.append("serve")

    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Track this process
    _register_process(port, proc)

    for _ in range(10):
        time.sleep(1)
        if is_chrome_running(port):  # Obscura exposes same CDP /json/version
            return proc

    return None


def find_browser(prefer_obscura: bool = True) -> str | None:
    """Find the best available browser for scraping.

    Args:
        prefer_obscura: If True, try Obscura first (lighter, anti-detection).
                       If False or Obscura not found, fall back to Chrome.

    Returns:
        Path to browser executable, or None if nothing found.
    """
    if prefer_obscura:
        obs = find_obscura()
        if obs:
            return obs
    return find_chrome()
