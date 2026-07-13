"""Unit tests for CDP lifecycle — process tracking, close_browser(), port conflicts.

ALL mocks — no real browser or subprocess.
"""

from unittest.mock import Mock, patch, MagicMock

import pytest

from cn_scraper_mcp.engines.cdp import (
    close_browser,
    close_all_browsers,
    launch_chrome,
    launch_obscura,
    is_chrome_running,
    _managed_processes,
    _register_process,
    _unregister_process,
    _is_our_port,
    _port_in_use,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def clear_managed_processes():
    """Each test starts with a clean process registry."""
    _managed_processes.clear()
    yield
    _managed_processes.clear()


def _mock_popen(pid=12345, is_alive=True):
    """Create a mock subprocess.Popen."""
    p = MagicMock()
    p.pid = pid
    p.poll.return_value = None if is_alive else 0
    return p


# ═══════════════════════════════════════════════════════════════
# _port_in_use
# ═══════════════════════════════════════════════════════════════


def test_port_in_use_true():
    """Port is in use when CDP /json/version responds."""
    with patch("cn_scraper_mcp.engines.cdp.urllib.request.urlopen") as mock:
        mock.return_value = MagicMock()
        assert _port_in_use(9222) is True


def test_port_in_use_false():
    """Port is NOT in use when CDP fails to connect."""
    with patch("cn_scraper_mcp.engines.cdp.urllib.request.urlopen",
               side_effect=OSError("connection refused")):
        assert _port_in_use(9222) is False


# ═══════════════════════════════════════════════════════════════
# _is_our_port
# ═══════════════════════════════════════════════════════════════


def test_is_our_port_true():
    """Returns True when port has a live process we launched."""
    p = _mock_popen(is_alive=True)
    _register_process(9222, p)
    assert _is_our_port(9222) is True


def test_is_our_port_false_dead_process():
    """Returns False when our process has already exited."""
    p = _mock_popen(is_alive=False)
    _register_process(9222, p)
    assert _is_our_port(9222) is False


def test_is_our_port_false_not_ours():
    """Returns False when port not in registry at all."""
    assert _is_our_port(9999) is False


# ═══════════════════════════════════════════════════════════════
# launch_chrome — returns Popen handle
# ═══════════════════════════════════════════════════════════════


@patch("cn_scraper_mcp.engines.cdp._os.path.exists", return_value=False)
@patch("cn_scraper_mcp.engines.cdp._os.makedirs")
@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.is_chrome_running", side_effect=[False, True])
@patch("cn_scraper_mcp.engines.cdp.find_chrome", return_value="C:/chrome.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=False)
def test_launch_chrome_returns_popen(mock_port_in_use, mock_find, mock_is_running,
                                      mock_popen, mock_makedirs, mock_exists):
    """launch_chrome() returns a subprocess.Popen object, not bool."""
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.poll.return_value = None  # process still running
    mock_popen.return_value = mock_proc

    result = launch_chrome(9247, "/tmp/profile", url="https://jd.com")

    assert result is mock_proc
    assert isinstance(result, MagicMock)  # stands in for subprocess.Popen
    # Should have been registered
    assert _is_our_port(9247) is True


# ═══════════════════════════════════════════════════════════════
# launch_chrome — port conflict detection
# ═══════════════════════════════════════════════════════════════


@patch("cn_scraper_mcp.engines.cdp._os.path.exists", return_value=False)
@patch("cn_scraper_mcp.engines.cdp._os.makedirs")
@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.find_chrome", return_value="C:/chrome.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=True)
@patch("cn_scraper_mcp.engines.cdp._is_our_port", return_value=False)
def test_launch_chrome_port_conflict_external(mock_ours, mock_port, mock_find,
                                               mock_popen, mock_makedirs, mock_exists):
    """Raises RuntimeError when port is busy with a non-ours process."""
    with pytest.raises(RuntimeError, match="already in use"):
        launch_chrome(9247, "/tmp/profile")


@patch("cn_scraper_mcp.engines.cdp._os.path.exists", return_value=False)
@patch("cn_scraper_mcp.engines.cdp._os.makedirs")
@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.find_chrome", return_value="C:/chrome.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=True)
@patch("cn_scraper_mcp.engines.cdp._is_our_port", return_value=True)
def test_launch_chrome_port_conflict_ours(mock_ours, mock_port, mock_find,
                                           mock_popen, mock_makedirs, mock_exists):
    """Returns existing handle when port is busy with OUR process."""
    our_proc = _mock_popen(pid=1, is_alive=True)
    _register_process(9247, our_proc)

    result = launch_chrome(9247, "/tmp/profile")

    assert result is our_proc
    mock_popen.assert_not_called()  # no new launch


# ═══════════════════════════════════════════════════════════════
# launch_chrome — SingletonLock handling
# ═══════════════════════════════════════════════════════════════


@patch("cn_scraper_mcp.engines.cdp._os.path.exists", return_value=True)
@patch("cn_scraper_mcp.engines.cdp._os.remove")
@patch("cn_scraper_mcp.engines.cdp._os.makedirs")
@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.is_chrome_running", side_effect=[False, True])
@patch("cn_scraper_mcp.engines.cdp.find_chrome", return_value="C:/chrome.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=False)
def test_launch_chrome_singleton_lock_removed(mock_port, mock_find, mock_running,
                                               mock_popen, mock_makedirs, mock_remove,
                                               mock_exists):
    """SingletonLock is removed before launch."""
    mock_proc = MagicMock(); mock_proc.pid = 42
    mock_proc.poll.return_value = None  # process still running
    mock_popen.return_value = mock_proc

    result = launch_chrome(9247, "/tmp/profile")

    mock_remove.assert_called_once()
    assert result is mock_proc


@patch("cn_scraper_mcp.engines.cdp._os.path.exists", return_value=True)
@patch("cn_scraper_mcp.engines.cdp._os.remove", side_effect=PermissionError("Access denied"))
@patch("cn_scraper_mcp.engines.cdp._os.makedirs")
@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.find_chrome", return_value="C:/chrome.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=False)
def test_launch_chrome_singleton_lock_fails(mock_port, mock_find, mock_popen,
                                              mock_makedirs, mock_remove, mock_exists):
    """Raises RuntimeError when SingletonLock cannot be removed."""
    with pytest.raises(RuntimeError, match="Cannot remove Chrome SingletonLock"):
        launch_chrome(9247, "/tmp/profile")


# ═══════════════════════════════════════════════════════════════
# close_browser — terminates ONLY our process
# ═══════════════════════════════════════════════════════════════


def test_close_browser_terminates_our_process():
    """close_browser terminates a process we launched and returns True."""
    proc = _mock_popen(is_alive=True)
    _register_process(9247, proc)

    result = close_browser(9247)

    assert result is True
    proc.terminate.assert_called_once()
    # Also removed from registry
    assert 9247 not in _managed_processes


def test_close_browser_not_ours():
    """close_browser returns False when port not tracked."""
    result = close_browser(9999)
    assert result is False


def test_close_browser_already_dead():
    """close_browser returns False when process already exited."""
    proc = _mock_popen(is_alive=False)
    _register_process(9247, proc)

    result = close_browser(9247)

    assert result is False
    proc.terminate.assert_not_called()
    assert 9247 not in _managed_processes


def test_close_browser_kills_on_timeout():
    """close_browser escalates to kill() when terminate() times out."""
    proc = _mock_popen(is_alive=True)
    proc.wait.side_effect = [__import__("subprocess").TimeoutExpired("wait", 5), None]
    _register_process(9247, proc)

    result = close_browser(9247)

    assert result is True
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
    assert 9247 not in _managed_processes


# ═══════════════════════════════════════════════════════════════
# close_all_browsers
# ═══════════════════════════════════════════════════════════════


def test_close_all_browsers_terminates_all():
    """close_all_browsers terminates every tracked process."""
    p1 = _mock_popen(pid=1, is_alive=True)
    p2 = _mock_popen(pid=2, is_alive=True)
    _register_process(9247, p1)
    _register_process(9222, p2)

    count = close_all_browsers()

    assert count == 2
    p1.terminate.assert_called_once()
    p2.terminate.assert_called_once()
    assert len(_managed_processes) == 0


def test_close_all_browsers_mixed():
    """close_all_browsers skips dead processes, counts only killed ones."""
    p1 = _mock_popen(pid=1, is_alive=True)
    p2 = _mock_popen(pid=2, is_alive=False)
    _register_process(9247, p1)
    _register_process(9222, p2)

    count = close_all_browsers()

    assert count == 1
    p1.terminate.assert_called_once()
    p2.terminate.assert_not_called()
    assert len(_managed_processes) == 0


# ═══════════════════════════════════════════════════════════════
# launch_obscura — returns Popen handle
# ═══════════════════════════════════════════════════════════════


@patch("cn_scraper_mcp.engines.cdp.subprocess.Popen")
@patch("cn_scraper_mcp.engines.cdp.is_chrome_running", side_effect=[False, True])
@patch("cn_scraper_mcp.engines.cdp.find_obscura", return_value="C:/obscura.exe")
@patch("cn_scraper_mcp.engines.cdp._port_in_use", return_value=False)
def test_launch_obscura_returns_popen(mock_port, mock_find, mock_running, mock_popen):
    """launch_obscura() returns a subprocess.Popen object, not bool."""
    mock_proc = MagicMock(); mock_proc.pid = 42
    mock_proc.poll.return_value = None  # process still running
    mock_popen.return_value = mock_proc

    result = launch_obscura(port=9222, stealth=True)

    assert result is mock_proc
    assert _is_our_port(9222) is True


# ═══════════════════════════════════════════════════════════════
# JDEngine.close_chrome uses close_browser (integration check)
# ═══════════════════════════════════════════════════════════════


def test_jd_engine_close_chrome_calls_close_browser():
    """JDEngine.close_chrome() delegates to close_browser(port)."""
    from cn_scraper_mcp.engines.jd import JDEngine

    engine = JDEngine(port=9247)

    with patch("cn_scraper_mcp.engines.jd.close_browser") as mock_cb:
        engine.close_chrome()
        mock_cb.assert_called_once_with(9247)


# ═══════════════════════════════════════════════════════════════
# XiaohongshuEngine.cleanup uses close_browser
# ═══════════════════════════════════════════════════════════════


def test_xhs_cleanup_calls_close_browser():
    """XiaohongshuEngine.cleanup() delegates to close_browser for both ports."""
    from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

    engine = XiaohongshuEngine(port=9251)

    with patch("cn_scraper_mcp.engines.xiaohongshu.close_browser") as mock_cb:
        engine.cleanup()
        # Should be called for both XHS_PORT and OBSCURA_PORT
        assert mock_cb.call_count == 2
        mock_cb.assert_any_call(9251)
        mock_cb.assert_any_call(9222)


def test_xhs_cleanup_skips_obscura_when_same_port():
    """When port IS Obscura port, don't double-call."""
    from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

    engine = XiaohongshuEngine(port=9222)

    with patch("cn_scraper_mcp.engines.xiaohongshu.close_browser") as mock_cb:
        engine.cleanup()
        # Only one call since port==OBSCURA_PORT
        mock_cb.assert_called_once_with(9222)
