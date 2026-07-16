"""Local, non-scraping environment diagnostics."""

import os
import platform
import shutil
import socket
import subprocess
import sys

from cn_scraper_mcp import __version__
from cn_scraper_mcp.auth import check_all_cookies
from cn_scraper_mcp.logging import get_recent_errors

DIAGNOSE_TIMEOUT = 5


def diagnose_environment() -> dict:
    result = {
        "platform": {
            "package_version": __version__,
            "python_version": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
            "os": platform.system(),
            "os_release": platform.release(),
        },
        "dependencies": {},
        "browsers": {},
        "cdp_ports": {},
        "cookies": {},
        "diagnostics": {"recent_errors": get_recent_errors()},
    }
    for name in ("fastmcp", "curl_cffi", "websockets", "dotenv"):
        result["dependencies"][name] = check_dependency(name)
    result["browsers"]["chrome"] = check_chrome()
    result["browsers"]["obscura"] = check_obscura()
    for port in (9222, 9247, 9251):
        result["cdp_ports"][str(port)] = check_port(port)
    try:
        result["cookies"] = check_all_cookies()
    except Exception as exc:
        result["cookies"] = {"error": str(exc)}
    return result


def check_dependency(name: str) -> dict:
    try:
        module = __import__(name)
        return {"installed": True, "version": getattr(module, "__version__", "unknown")}
    except ImportError:
        return {"installed": False, "version": None}
    except Exception as exc:
        return {"installed": False, "version": None, "error": str(exc)[:100]}


def check_chrome() -> dict:
    result: dict = {"found": False, "path": None, "version": None}
    configured = os.environ.get("CHROME_PATH")
    if configured and os.path.exists(configured):
        result.update(found=True, path=configured)
    else:
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                shutil.which("chrome"),
            ]
        elif sys.platform == "darwin":
            candidates = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                shutil.which("google-chrome"),
                shutil.which("chrome"),
            ]
        else:
            candidates = [
                shutil.which(name)
                for name in (
                    "google-chrome",
                    "google-chrome-stable",
                    "chromium",
                    "chromium-browser",
                    "chrome",
                )
            ]
        for candidate in candidates:
            if candidate and os.path.isfile(candidate):
                result.update(found=True, path=candidate)
                break

    if result["found"]:
        try:
            completed = subprocess.run(
                [result["path"], "--version"],
                capture_output=True,
                text=True,
                timeout=DIAGNOSE_TIMEOUT,
            )
            result["version"] = completed.stdout.strip() or completed.stderr.strip()
        except (subprocess.TimeoutExpired, Exception):
            result["version"] = "timeout"
    return result


def check_obscura() -> dict:
    path = shutil.which("obscura")
    return {"found": bool(path), "path": path}


def check_port(port: int) -> dict:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(DIAGNOSE_TIMEOUT)
            return {"in_use": sock.connect_ex(("127.0.0.1", port)) == 0}
    except (TimeoutError, OSError, Exception):
        return {"in_use": False, "error": "timeout"}
