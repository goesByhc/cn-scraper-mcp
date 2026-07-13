"""Shared HTTP client with timeout, retry, backoff, and rate limiting.

Usage:
    from cn_scraper_mcp.http import HttpClient

    client = HttpClient(timeout=15, max_retries=3)
    status, data = client.get_json("https://api.example.com/data", headers={"User-Agent": "..."})
    # On success: (200, {"results": [...]})
    # On failure: (0, {"error": "timeout after 3 retries"}) or (500, {"error": "server error"})

    # For raw text (no JSON parsing):
    status, text = client.get_text("https://example.com/page.html")

    # With a custom session (e.g., curl_cffi for TLS fingerprinting):
    from curl_cffi import requests as creq
    session = creq.Session(impersonate="chrome")
    status, data = client.get_json(url, session=session)
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger("cn_scraper_mcp.http")

# Default User-Agent if none provided
_DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"


class HttpClient:
    """HTTP client with reliability features.

    - Configurable timeout (default 15s)
    - Retries on timeout, connection error, and 5xx (NOT 4xx/auth)
    - Exponential backoff: 1s → 2s → 4s (max 3 retries)
    - Checks Content-Type is application/json before parsing
    - Checks HTTP status before attempting JSON parse
    - Per-host rate limiting (minimum interval between requests)
    - Sanitized logging: host+path, never cookies or response body
    - Accepts custom headers (User-Agent, Cookie)
    """

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        rate_limit_interval: float = 0.5,
        default_headers: Optional[dict[str, str]] = None,
    ):
        """Create a new HttpClient.

        Args:
            timeout: Request timeout in seconds (default 15).
            max_retries: Maximum retry attempts (default 3, total 4 attempts).
            backoff_base: Base seconds for exponential backoff. Attempts
                          sleep: base, base*2, base*4, ...
            rate_limit_interval: Minimum seconds between requests to the same host.
            default_headers: Headers included on every request (e.g. User-Agent).
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.rate_limit_interval = rate_limit_interval
        self.default_headers = default_headers or {}
        self._last_request_time: dict[str, float] = {}

    # ── public API ──────────────────────────────────────────────

    def get_json(
        self,
        url: str,
        *,
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        session: Any = None,
    ) -> tuple[int, dict]:
        """GET a URL and return parsed JSON.

        Handles retry, backoff, rate limiting, Content-Type checks.

        Args:
            url: Full URL to fetch.
            params: Query string parameters (appended to URL).
            headers: Additional HTTP headers (merged with defaults).
            session: Optional requests-like session (curl_cffi.Session).
                     When provided, uses session.get() instead of urllib.

        Returns:
            (status_code, dict) — the parsed JSON on success, or
            {"error": "..."} dict with status_code=0 on transport errors.
        """
        status, body = self._request(
            "GET", url, params=params, headers=headers, session=session
        )
        if status == 0:
            return (0, body)  # already an error dict

        # Check Content-Type before attempting JSON parse
        ct = body.get("_content_type", "")
        if ct and "application/json" not in ct:
            detail = body.get("_raw", "")[:300]
            return (status, {"error": f"Unexpected Content-Type: {ct}", "detail": detail})

        # Attempt JSON parse
        raw = body.get("_raw", "")
        if not raw:
            return (status, {"error": "Empty response body"})

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return (status, {"error": f"JSON parse failed: {str(e)[:200]}", "detail": raw[:300]})

        return (status, data)

    def get_text(
        self,
        url: str,
        *,
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        session: Any = None,
    ) -> tuple[int, str]:
        """GET a URL and return the raw response text.

        Same retry/backoff/rate-limit behavior as get_json, but
        returns raw text without JSON parsing.

        Returns:
            (status_code, text) — text on success, error string on failure.
        """
        status, body = self._request(
            "GET", url, params=params, headers=headers, session=session
        )
        if status == 0:
            return (0, body.get("error", "Unknown error"))
        return (status, body.get("_raw", ""))

    # ── internal ────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
        session: Any = None,
    ) -> tuple[int, dict]:
        """Core request with retry loop.

        Returns:
            (status_code, {"_raw": response_body_str, "_content_type": str})
            On transport error: (0, {"error": str})
        """
        # Build final URL with query params
        full_url = url
        if params:
            qs = urllib.parse.urlencode(params)
            full_url = f"{url}?{qs}" if "?" not in url else f"{url}&{qs}"

        # Merge headers: default → per-call
        merged_headers = dict(self.default_headers)
        if headers:
            merged_headers.update(headers)

        # Ensure User-Agent is set (if user didn't explicitly remove it)
        if "User-Agent" not in merged_headers:
            merged_headers["User-Agent"] = _DEFAULT_UA

        # Rate limiting
        host = urlparse(full_url).netloc
        if host:
            self._rate_limit(host)

        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                result = self._do_request(
                    method, full_url, merged_headers, session=session
                )
                status = result.get("_status", 0)
                raw = result.get("_raw", "")
                ct = result.get("_content_type", "")

                # Sanitized logging: host + path, no cookies/body
                parsed = urlparse(full_url)
                short_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                logger.info(
                    "[HttpClient] %s %s → %d (attempt %d/%d)",
                    method,
                    short_url,
                    status,
                    attempt + 1,
                    self.max_retries + 1,
                )

                # Retry on 5xx, not 4xx
                if 500 <= status < 600:
                    if attempt < self.max_retries:
                        delay = self.backoff_base * (2 ** attempt)
                        logger.warning(
                            "[HttpClient] %s %s → %d, retrying in %.1fs",
                            method, short_url, status, delay,
                        )
                        time.sleep(delay)
                        continue
                    # Max retries exhausted on 5xx
                    return (status, {"error": f"Server error {status} after {self.max_retries + 1} attempts", "detail": raw[:300], "_status": status, "_content_type": ct})

                # Success (2xx) or client error (4xx) — don't retry
                return (status, result)

            except urllib.error.HTTPError as e:
                status = e.code
                short_url = self._short_url(full_url)
                logger.info(
                    "[HttpClient] %s %s → %d (attempt %d/%d)",
                    method, short_url, status,
                    attempt + 1, self.max_retries + 1,
                )
                # Read body if available (fp may be None)
                try:
                    body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                except Exception:
                    body = ""
                if 400 <= status < 500:
                    # 4xx — don't retry
                    return (status, {"_raw": body, "_content_type": e.headers.get("Content-Type", ""), "_status": status})
                if 500 <= status < 600 and attempt < self.max_retries:
                    delay = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        "[HttpClient] %s %s → %d, retrying in %.1fs",
                        method, short_url, status, delay,
                    )
                    time.sleep(delay)
                    continue
                return (status, {"_raw": body, "_content_type": e.headers.get("Content-Type", ""), "_status": status})

            except (urllib.error.URLError, TimeoutError, OSError, ConnectionError) as e:
                last_error = str(e)
                short_url = self._short_url(full_url)
                if attempt < self.max_retries:
                    delay = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        "[HttpClient] %s %s → %s, retrying in %.1fs (attempt %d/%d)",
                        method, short_url, type(e).__name__, delay,
                        attempt + 1, self.max_retries + 1,
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    "[HttpClient] %s %s → %s after all retries",
                    method, short_url, type(e).__name__,
                )
                return (0, {"error": f"Connection failed after {self.max_retries + 1} attempts: {last_error}"})

            except Exception as e:
                last_error = str(e)
                short_url = self._short_url(full_url)
                logger.error(
                    "[HttpClient] %s %s → unexpected error: %s",
                    method, short_url, type(e).__name__,
                )
                return (0, {"error": f"Unexpected error: {last_error}"})

        # Should not reach here, but guard
        return (0, {"error": f"Request failed: {last_error or 'unknown'}"})

    def _do_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        session: Any = None,
    ) -> dict:
        """Execute a single HTTP request (no retry).

        Returns:
            {"_raw": str, "_content_type": str, "_status": int}
        """
        if session is not None:
            # Use provided session (e.g., curl_cffi.Session)
            resp = session.request(method, url, headers=headers, timeout=self.timeout)
            return {
                "_raw": resp.text,
                "_content_type": resp.headers.get("Content-Type", ""),
                "_status": resp.status_code,
            }

        # Fallback: stdlib urllib
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "_raw": body,
                "_content_type": resp.headers.get("Content-Type", ""),
                "_status": resp.status,
            }
        except urllib.error.HTTPError as e:
            # Let caller handle retry logic — just pass through
            raise

    def _rate_limit(self, host: str) -> None:
        """Enforce minimum interval between requests to the same host."""
        now = time.monotonic()
        last = self._last_request_time.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed < self.rate_limit_interval:
                time.sleep(self.rate_limit_interval - elapsed)
        self._last_request_time[host] = time.monotonic()

    @staticmethod
    def _short_url(url: str) -> str:
        """Return scheme://host/path for logging (no query, no credentials)."""
        p = urlparse(url)
        # Use hostname to strip auth credentials (user:pass@)
        host = p.hostname or p.netloc
        if p.port:
            host = f"{host}:{p.port}"
        return f"{p.scheme}://{host}{p.path}"
