"""Unit tests for HttpClient — timeout, retry, backoff, rate limit, JSON handling.

ALL mocks — no real network requests.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from cn_scraper_mcp.http import HttpClient

# ── Helpers ────────────────────────────────────────────────────────────────

def _mock_response(status=200, body="", content_type="application/json"):
    """Create a mock response object for urllib.request.urlopen."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8") if isinstance(body, str) else body
    resp.headers = {"Content-Type": content_type}
    return resp


def _mock_urlopen_with_responses(responses):
    """Return a side_effect function that yields mock responses in order.

    Each element is either a mock response or an Exception.
    """
    def side_effect(*args, **kwargs):
        if not responses:
            raise RuntimeError("No more mock responses")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return side_effect


def _json_body(data):
    return json.dumps(data)


# ── HttpClient init ────────────────────────────────────────────────────────

class TestHttpClientInit:
    def test_defaults(self):
        client = HttpClient()
        assert client.timeout == 15.0
        assert client.max_retries == 3
        assert client.backoff_base == 1.0
        assert client.rate_limit_interval == 0.5
        assert client.default_headers == {}

    def test_custom_values(self):
        client = HttpClient(
            timeout=30,
            max_retries=2,
            backoff_base=2.0,
            rate_limit_interval=1.0,
            default_headers={"X-Custom": "val"},
        )
        assert client.timeout == 30.0
        assert client.max_retries == 2
        assert client.backoff_base == 2.0
        assert client.default_headers == {"X-Custom": "val"}


# ── get_json: success ──────────────────────────────────────────────────────

class TestGetJsonSuccess:
    def test_successful_json_response(self):
        client = HttpClient(timeout=10, max_retries=0)
        resp = _mock_response(200, _json_body({"ok": True, "data": [1, 2, 3]}))

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert data == {"ok": True, "data": [1, 2, 3]}

    def test_custom_headers_merged_with_defaults(self):
        client = HttpClient(max_retries=0, default_headers={"X-Default": "yes"})
        resp = _mock_response(200, _json_body({"ok": True}))

        with patch("urllib.request.Request") as mock_req_class:
            mock_req = MagicMock()
            mock_req_class.return_value = mock_req

            with patch("urllib.request.urlopen", return_value=resp):
                status, data = client.get_json(
                    "https://api.example.com/data",
                    headers={"X-Custom": "val"},
                )

            # Check that the Request was created with merged headers
            call_args = mock_req_class.call_args
            req_headers = call_args[1].get("headers", {})
            assert req_headers.get("X-Default") == "yes"
            assert req_headers.get("X-Custom") == "val"

    def test_user_agent_set_by_default(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, _json_body({"ok": True}))

        with patch("urllib.request.Request") as mock_req_class:
            mock_req = MagicMock()
            mock_req_class.return_value = mock_req
            with patch("urllib.request.urlopen", return_value=resp):
                client.get_json("https://api.example.com/data")

            headers = mock_req_class.call_args[1].get("headers", {})
            assert "User-Agent" in headers

    def test_query_params_appended_to_url(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, _json_body({"ok": True}))

        with patch("urllib.request.Request") as mock_req_class:
            mock_req = MagicMock()
            mock_req_class.return_value = mock_req
            with patch("urllib.request.urlopen", return_value=resp):
                client.get_json(
                    "https://api.example.com/search",
                    params={"q": "test", "page": "1"},
                )

            url_used = mock_req_class.call_args[0][0]
            assert "q=test" in url_used
            assert "page=1" in url_used


# ── get_json: Content-Type checks ──────────────────────────────────────────

class TestGetJsonContentType:
    def test_non_json_content_type(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, "<html>hello</html>", content_type="text/html")

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert "error" in data
        assert "Content-Type" in data["error"]
        assert "text/html" in data["error"]

    def test_missing_content_type_ok(self):
        """Missing Content-Type should still attempt JSON parse."""
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, _json_body({"ok": True}), content_type="")

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert data == {"ok": True}

    def test_application_json_with_charset_ok(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, _json_body({"ok": True}),
                              content_type="application/json; charset=utf-8")

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert data == {"ok": True}


# ── get_json: invalid JSON ─────────────────────────────────────────────────

class TestGetJsonInvalidJson:
    def test_invalid_json_body(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, "not valid json {{{", content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert "error" in data
        assert "JSON parse" in data["error"]

    def test_empty_response_body(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, "", content_type="application/json")

        with patch("urllib.request.urlopen", return_value=resp):
            status, data = client.get_json("https://api.example.com/data")

        assert status == 200
        assert "error" in data
        assert "Empty" in data["error"]


# ── get_json: 4xx — no retry ──────────────────────────────────────────────

class TestGetJson4xx:
    def test_403_no_retry(self):
        client = HttpClient(max_retries=3, backoff_base=0.01)
        from urllib.error import HTTPError

        # HTTPError with 403
        http_err = HTTPError(
            "https://api.example.com/data", 403, "Forbidden",
            {"Content-Type": "application/json"}, None
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with patch("time.sleep") as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        # 4xx — no sleep (no retry)
        mock_sleep.assert_not_called()
        assert status == 403

    def test_401_no_retry(self):
        client = HttpClient(max_retries=3, backoff_base=0.01)
        from urllib.error import HTTPError

        http_err = HTTPError(
            "https://api.example.com/data", 401, "Unauthorized",
            {"Content-Type": "application/json"}, None
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with patch("time.sleep") as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        mock_sleep.assert_not_called()
        assert status == 401

    def test_404_no_retry(self):
        client = HttpClient(max_retries=3, backoff_base=0.01)
        from urllib.error import HTTPError

        http_err = HTTPError(
            "https://api.example.com/data", 404, "Not Found",
            {"Content-Type": "application/json"}, None
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with patch("time.sleep") as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        mock_sleep.assert_not_called()
        assert status == 404


# ── get_json: 5xx — retry with backoff ─────────────────────────────────────

class TestGetJson5xx:
    def test_500_retries_then_gives_up(self):
        client = HttpClient(max_retries=2, backoff_base=0.01)
        from urllib.error import HTTPError

        # All attempts return 500
        http_err = HTTPError(
            "https://api.example.com/data", 500, "Server Error",
            {"Content-Type": "application/json"}, None
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with patch("time.sleep", return_value=None) as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        # max_retries=2 → 3 total attempts, 2 retries → 2 sleeps
        assert mock_sleep.call_count == 2
        assert status == 500
        assert "error" in data
        # Error could be about JSON parse failing (empty body) or server error
        assert any(kw in data["error"] for kw in ["after 3 attempts", "Empty", "JSON parse"])

    def test_503_retry_then_success(self):
        client = HttpClient(max_retries=3, backoff_base=0.01)
        from urllib.error import HTTPError

        # First attempt: 503 → retry → success
        responses = [
            HTTPError("https://api.example.com/data", 503, "Unavailable",
                      {"Content-Type": "application/json"}, None),
            _mock_response(200, _json_body({"ok": True})),
        ]

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen_with_responses(responses)):
            with patch("time.sleep", return_value=None) as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        assert mock_sleep.call_count == 1
        assert status == 200
        assert data == {"ok": True}

    def test_backoff_increases_exponentially(self):
        client = HttpClient(max_retries=3, backoff_base=1.0)
        from urllib.error import HTTPError

        http_err = HTTPError(
            "https://api.example.com/data", 500, "Error",
            {"Content-Type": "application/json"}, None
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with patch("time.sleep", return_value=None) as mock_sleep:
                client.get_json("https://api.example.com/data")

        # 4 attempts total, 3 retries → 3 sleeps: 1.0, 2.0, 4.0
        assert mock_sleep.call_count == 3
        sleep_times = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_times == [1.0, 2.0, 4.0]


# ── get_json: timeout / connection errors ──────────────────────────────────

class TestGetJsonConnectionErrors:
    def test_timeout_retries(self):
        client = HttpClient(max_retries=3, backoff_base=0.01)

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with patch("time.sleep", return_value=None) as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        # 4 attempts, 3 retries → 3 sleeps
        assert mock_sleep.call_count == 3
        assert status == 0
        assert "error" in data
        assert "Connection failed" in data["error"]
        assert "timed out" in data["error"]

    def test_urlerror_retries(self):
        from urllib.error import URLError
        client = HttpClient(max_retries=2, backoff_base=0.01)

        with patch("urllib.request.urlopen", side_effect=URLError("refused")):
            with patch("time.sleep", return_value=None) as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        assert mock_sleep.call_count == 2
        assert status == 0
        assert "refused" in data["error"]

    def test_connection_refused_then_success(self):
        from urllib.error import URLError
        client = HttpClient(max_retries=3, backoff_base=0.01)

        responses = [
            URLError("connection refused"),
            URLError("connection refused"),
            _mock_response(200, _json_body({"recovered": True})),
        ]

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen_with_responses(responses)):
            with patch("time.sleep", return_value=None) as mock_sleep:
                status, data = client.get_json("https://api.example.com/data")

        assert mock_sleep.call_count == 2
        assert status == 200
        assert data == {"recovered": True}

    def test_oserror_retries(self):
        client = HttpClient(max_retries=1, backoff_base=0.01)

        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            with patch("time.sleep", return_value=None):
                status, data = client.get_json("https://api.example.com/data")

        assert status == 0
        assert "network down" in data["error"]


# ── get_text ───────────────────────────────────────────────────────────────

class TestGetText:
    def test_get_text_success(self):
        client = HttpClient(max_retries=0)
        resp = _mock_response(200, "<html>Hello World</html>", content_type="text/html")

        with patch("urllib.request.urlopen", return_value=resp):
            status, text = client.get_text("https://example.com/page")

        assert status == 200
        assert text == "<html>Hello World</html>"

    def test_get_text_error(self):
        from urllib.error import URLError
        client = HttpClient(max_retries=0)

        with patch("urllib.request.urlopen", side_effect=URLError("refused")):
            status, text = client.get_text("https://example.com/page")

        assert status == 0
        assert "refused" in text


# ── rate limiting ──────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_rate_limit_enforces_min_interval(self):
        client = HttpClient(max_retries=0, rate_limit_interval=0.3)
        resp = _mock_response(200, _json_body({"ok": True}))

        with patch("urllib.request.urlopen", return_value=resp):
            with patch("time.sleep", wraps=time.sleep) as mock_sleep:
                # Two requests to same host
                client.get_json("https://api.example.com/a")
                client.get_json("https://api.example.com/b")

        # Second request should have triggered a sleep
        sleep_calls = [
            c for c in mock_sleep.call_args_list
            if c.args and c.args[0] > 0
        ]
        assert len(sleep_calls) >= 1

    def test_rate_limit_different_hosts_no_delay(self):
        client = HttpClient(max_retries=0, rate_limit_interval=10.0)
        resp = _mock_response(200, _json_body({"ok": True}))

        with patch("urllib.request.urlopen", return_value=resp):
            with patch("time.sleep", wraps=time.sleep) as mock_sleep:
                client.get_json("https://api.example.com/a")
                client.get_json("https://api.other.com/b")

        # Different hosts — no rate-limit sleep needed
        rate_limit_sleeps = [
            c for c in mock_sleep.call_args_list
            if c.args and c.args[0] >= 1.0
        ]
        assert len(rate_limit_sleeps) == 0


# ── session parameter (curl_cffi / custom) ─────────────────────────────────

class TestGetJsonWithSession:
    def test_session_is_used_when_provided(self):
        client = HttpClient(max_retries=0)
        mock_session = MagicMock()
        mock_session.request.return_value = _mock_response(
            200, _json_body({"from_session": True})
        )
        # Add .text and .status_code to mimic requests-like API
        mock_session.request.return_value.text = _json_body({"from_session": True})
        mock_session.request.return_value.status_code = 200

        with patch("urllib.request.urlopen") as mock_urlopen:
            status, data = client.get_json(
                "https://api.example.com/data",
                session=mock_session,
            )
            # urlopen should NOT be called
            mock_urlopen.assert_not_called()
            # session.request should be called
            mock_session.request.assert_called_once()

        assert status == 200
        assert data == {"from_session": True}

    def test_session_receives_redirect_policy(self):
        client = HttpClient(max_retries=0)
        mock_session = MagicMock()
        mock_session.request.return_value.text = "ok"
        mock_session.request.return_value.status_code = 200
        mock_session.request.return_value.headers = {"Content-Type": "text/plain"}

        client.get_text(
            "https://api.example.com/data",
            session=mock_session,
            follow_redirects=False,
        )

        assert mock_session.request.call_args.kwargs["allow_redirects"] is False

    def test_session_with_5xx_retries(self):
        client = HttpClient(max_retries=1, backoff_base=0.01)
        mock_session = MagicMock()

        resp1 = MagicMock()
        resp1.status_code = 500
        resp1.text = _json_body({"error": "server error"})
        resp1.headers = {"Content-Type": "application/json"}

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.text = _json_body({"ok": True})
        resp2.headers = {"Content-Type": "application/json"}

        mock_session.request.side_effect = [resp1, resp2]

        with patch("time.sleep", return_value=None) as mock_sleep:
            status, data = client.get_json(
                "https://api.example.com/data",
                session=mock_session,
            )

        assert mock_sleep.call_count == 1
        assert status == 200
        assert data == {"ok": True}


def test_follow_redirects_false_does_not_follow_stdlib_redirect():
    visited = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visited.append(self.path)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/target")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"FOLLOWED")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/start"
        status, body = HttpClient(max_retries=0).get_text(
            url,
            follow_redirects=False,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert status == 302
    assert body == ""
    assert visited == ["/start"]


# ── logging (sanitized: host+path, no cookies) ─────────────────────────────

class TestLogging:
    def test_short_url_strips_query_and_credentials(self):
        url = "https://user:pass@api.example.com:8080/path/to/resource?q=secret&token=abc"
        short = HttpClient._short_url(url)
        assert short == "https://api.example.com:8080/path/to/resource"
        assert "secret" not in short
        assert "token" not in short
        assert "user" not in short
        assert "pass" not in short


# ── http.py module can be imported without errors ──────────────────────────

class TestModuleImport:
    def test_import(self):
        """Sanity check that the module imports cleanly."""
        from cn_scraper_mcp import http
        assert hasattr(http, "HttpClient")
