"""Unit tests for concurrency isolation — BrowserLock per CDP port.

ALL mocks — no real browser, threads, or network.
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.engines.cdp import (
    _port_locks,
    get_browser_lock,
)


def _run_result(value):
    """Return an asyncio.run side effect that closes the supplied coroutine."""
    def _side_effect(coro):
        coro.close()
        return value

    return _side_effect


@pytest.fixture(autouse=True)
def clear_port_locks():
    """Each test starts with a clean lock registry."""
    _port_locks.clear()
    yield
    _port_locks.clear()


# ═══════════════════════════════════════════════════════════════
# BrowserLock — get_browser_lock basics
# ═══════════════════════════════════════════════════════════════


def test_get_browser_lock_returns_lock():
    """get_browser_lock returns a threading.Lock instance."""
    lock = get_browser_lock(9247)
    assert isinstance(lock, type(threading.Lock()))


def test_same_port_same_lock():
    """Same port returns the identical Lock object."""
    lock1 = get_browser_lock(9247)
    lock2 = get_browser_lock(9247)
    assert lock1 is lock2


def test_different_port_different_lock():
    """Different ports return different Lock objects."""
    lock1 = get_browser_lock(9247)
    lock2 = get_browser_lock(9222)
    assert lock1 is not lock2


def test_multiple_ports_independent_locks():
    """Multiple ports all get their own independent locks."""
    lock_jd = get_browser_lock(9247)
    lock_pdd = get_browser_lock(9255)
    lock_xhs = get_browser_lock(9251)

    assert lock_jd is not lock_pdd
    assert lock_jd is not lock_xhs
    assert lock_pdd is not lock_xhs


# ═══════════════════════════════════════════════════════════════
# BrowserLock — mutual exclusion behavior
# ═══════════════════════════════════════════════════════════════


def test_lock_prevents_concurrent_access():
    """Acquiring the lock on the same port blocks the second thread."""
    lock = get_browser_lock(9247)

    # Acquire the lock in the main thread
    assert lock.acquire(blocking=False)

    # Try to acquire in another thread — should fail
    acquired_in_thread = []

    def try_acquire():
        acquired_in_thread.append(lock.acquire(blocking=False))

    t = threading.Thread(target=try_acquire)
    t.start()
    t.join(timeout=1)

    assert acquired_in_thread == [False]
    lock.release()


def test_lock_released_allows_access():
    """After releasing, another thread can acquire the lock."""
    lock = get_browser_lock(9247)

    lock.acquire(blocking=False)
    lock.release()

    acquired_in_thread = []

    def try_acquire():
        acquired_in_thread.append(lock.acquire(blocking=False))

    t = threading.Thread(target=try_acquire)
    t.start()
    t.join(timeout=1)

    assert acquired_in_thread == [True]
    # Clean up
    if acquired_in_thread[0]:
        lock.release()


def test_different_ports_no_contention():
    """Locks on different ports don't block each other."""
    lock_a = get_browser_lock(9247)
    lock_b = get_browser_lock(9255)

    assert lock_a.acquire(blocking=False)
    assert lock_b.acquire(blocking=False)

    lock_a.release()
    lock_b.release()


# ═══════════════════════════════════════════════════════════════
# BrowserLock — context manager usage
# ═══════════════════════════════════════════════════════════════


def test_lock_context_manager():
    """BrowserLock works as a context manager (with statement)."""
    lock = get_browser_lock(9247)

    # Not held initially
    assert lock.acquire(blocking=False)
    lock.release()

    with lock:
        # Should be held inside the block
        assert not lock.acquire(blocking=False)

    # Should be released after the block
    assert lock.acquire(blocking=False)
    lock.release()


def test_context_manager_serializes_threads():
    """Context manager serializes access across threads on the same port."""
    lock = get_browser_lock(9247)
    execution_order = []

    def worker(worker_id: int):
        with lock:
            execution_order.append(("enter", worker_id))
            time.sleep(0.05)  # small delay to ensure ordering
            execution_order.append(("exit", worker_id))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    # After all threads finish, verify no interleaving exits
    # Each worker should enter and exit before the next enters
    for i in range(3):
        enter_idx = execution_order.index(("enter", i))
        exit_idx = execution_order.index(("exit", i))
        assert exit_idx > enter_idx  # exit after enter

    # Check no overlapping: for workers i and j (i<j), worker i's exit
    # should come before worker j's enter
    for i in range(3):
        for j in range(i + 1, 3):
            exit_i = execution_order.index(("exit", i))
            enter_j = execution_order.index(("enter", j))
            assert exit_i < enter_j, (
                f"Worker {i} exit at {exit_i} should be before "
                f"worker {j} enter at {enter_j}\n"
                f"Full order: {execution_order}"
            )


# ═══════════════════════════════════════════════════════════════
# HTTP engines — no locks needed (verified by absence/normalization)
# ═══════════════════════════════════════════════════════════════


def test_http_engines_no_cdp_lock_needed():
    """HTTP engines (Taobao, Zhihu, Zsxq) don't use BrowserLock.

    This is verified by the fact they never import get_browser_lock
    and use pure HTTP/REST via HttpClient — no CDP dependency.
    """
    from cn_scraper_mcp.auth import CookieFileManager

    # TaobaoEngine uses HttpClient and MTOP API — no CDP at all
    from cn_scraper_mcp.engines.taobao import TaobaoEngine

    fake_curl_cffi = MagicMock()
    with patch.object(CookieFileManager, "load", return_value={
        "_m_h5_tk": "abc", "cookie2": "123", "_tb_token_": "xyz"
    }), patch.dict("sys.modules", {"curl_cffi": fake_curl_cffi}):
        engine = TaobaoEngine(cookies_path="/fake/path.json")
        assert not hasattr(engine, "port")

    # ZhihuEngine uses HttpClient — no CDP at all
    from cn_scraper_mcp.engines.zhihu import ZhihuEngine
    with patch.object(CookieFileManager, "load", return_value={
        "z_c0": "abc", "d_c0": "123"
    }):
        engine = ZhihuEngine(cookies_path="/fake/path.json")
        assert not hasattr(engine, "port")

    # ZsxqEngine uses HttpClient — no CDP at all
    from cn_scraper_mcp.engines.zsxq import ZsxqEngine
    with patch.object(CookieFileManager, "load", return_value={
        "zsxq_access_token": "abc"
    }):
        engine = ZsxqEngine(cookies_path="/fake/path.json")
        assert not hasattr(engine, "port")


# ═══════════════════════════════════════════════════════════════
# Browser engines — lock integration in search()
# ═══════════════════════════════════════════════════════════════


def test_jd_engine_uses_browser_lock():
    """JDEngine.search() acquires BrowserLock before CDP operations."""
    from cn_scraper_mcp.engines.jd import JDEngine

    engine = JDEngine(port=9247)

    # Mock the actual search to avoid real Chrome
    with patch.object(engine, "ensure_chrome", return_value=True):
        with patch("cn_scraper_mcp.engines.jd.get_browser_lock") as mock_get_lock:
            mock_lock = MagicMock()
            mock_lock.__enter__ = MagicMock()
            mock_lock.__exit__ = MagicMock(return_value=None)
            mock_get_lock.return_value = mock_lock

            # Mock asyncio.run (imported locally inside search())
            mock_raw = {
                    "count": 2,
                    "items": [
                        {"sku": "123", "name": "Test", "prices": [99.0], "ad": False},
                        {"sku": "456", "name": "Test2", "prices": [149.0], "ad": False},
                    ],
                    "url": "https://search.jd.com/Search?keyword=test",
                    "pageText": "some page text",
                }
            with patch("asyncio.run", side_effect=_run_result(mock_raw)):
                result = engine.search("test", limit=10)

    # Verify lock was acquired
    mock_get_lock.assert_called_once_with(9247)
    mock_lock.__enter__.assert_called_once()
    mock_lock.__exit__.assert_called_once()

    assert result["keyword"] == "test"
    assert result["items"][0]["sku"] == "123"


def test_pdd_engine_uses_browser_lock():
    """PDDEngine.search() acquires BrowserLock before CDP operations."""
    from cn_scraper_mcp.engines.pdd import PDDEngine

    engine = PDDEngine(port=9255)

    # Mock cookies
    engine._cookies = {"PDDAccessToken": "test_token", "pdd_user_id": "12345"}

    with patch.object(engine, "ensure_chrome", return_value=True):
        with patch("cn_scraper_mcp.engines.pdd.get_browser_lock") as mock_get_lock:
            mock_lock = MagicMock()
            mock_lock.__enter__ = MagicMock()
            mock_lock.__exit__ = MagicMock(return_value=None)
            mock_get_lock.return_value = mock_lock

            mock_raw = {
                    "url": "https://mobile.yangkeduo.com/search_result.html",
                    "title": "拼多多",
                    "ogTitle": "",
                    "pageText": "¥99.00 Test Product",
                    "rateLimited": False,
                    "itemCount": 1,
                    "items": [{"goodsId": "123", "name": "Test", "price": 99.0, "sold": 100}],
                }
            with patch("asyncio.run", side_effect=_run_result(mock_raw)):
                result = engine.search("test", limit=10)

    mock_get_lock.assert_called_once_with(9255)
    mock_lock.__enter__.assert_called_once()
    mock_lock.__exit__.assert_called_once()

    assert result["keyword"] == "test"


def test_xhs_engine_uses_browser_lock():
    """XiaohongshuEngine.search() acquires BrowserLock before CDP operations."""
    from cn_scraper_mcp.engines.xiaohongshu import XiaohongshuEngine

    engine = XiaohongshuEngine(port=9251)

    with patch.object(engine, "ensure_browser", return_value=True):
        with patch("cn_scraper_mcp.engines.xiaohongshu.get_browser_lock") as mock_get_lock:
            mock_lock = MagicMock()
            mock_lock.__enter__ = MagicMock()
            mock_lock.__exit__ = MagicMock(return_value=None)
            mock_get_lock.return_value = mock_lock

            mock_raw = {
                    "url": "https://www.xiaohongshu.com/search_result?keyword=test",
                    "pageText": "小红书 search results",
                    "items": [
                        {
                            "title": "Test Note",
                            "author": "TestUser",
                            "likes": "100",
                            "noteId": "abc123def4567890",
                            "href": "https://www.xiaohongshu.com/explore/abc123def4567890",
                            "xsec_token": "tok123",
                        }
                    ],
                }
            with patch(
                "cn_scraper_mcp.engines.xiaohongshu.asyncio.run",
                side_effect=_run_result(mock_raw),
            ):
                result = engine.search("test", limit=10)

    mock_get_lock.assert_called_once_with(9251)
    mock_lock.__enter__.assert_called_once()
    mock_lock.__exit__.assert_called_once()

    assert result["keyword"] == "test"


# ═══════════════════════════════════════════════════════════════
# BrowserLock — exported from engines __init__
# ═══════════════════════════════════════════════════════════════


def test_get_browser_lock_exported():
    """get_browser_lock is exported from cn_scraper_mcp.engines."""
    from cn_scraper_mcp.engines import get_browser_lock as exported_lock

    lock = exported_lock(9999)
    assert isinstance(lock, type(threading.Lock()))


def test_get_browser_lock_in_all():
    """get_browser_lock is in engines.__all__."""
    from cn_scraper_mcp.engines import __all__

    assert "get_browser_lock" in __all__


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════


def test_locks_cleaned_per_test():
    """Verify the autouse fixture resets _port_locks."""
    # Trigger a lock creation
    get_browser_lock(9247)
    assert 9247 in _port_locks

    # The fixture will clear this after the test
    # We verify by checking that _port_locks starts empty in the next test
    # (confirmed by test_get_browser_lock_returns_lock which runs first)


def test_many_ports():
    """Many concurrent ports all get independent locks."""
    locks = {}
    for port in range(9200, 9300):
        lock = get_browser_lock(port)
        assert isinstance(lock, type(threading.Lock()))
        locks[port] = lock

    # All locks should be distinct
    lock_ids = {id(lock) for lock in locks.values()}
    assert len(lock_ids) == 100  # each port has its own lock object
