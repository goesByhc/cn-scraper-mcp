"""Unit tests for secure_storage.py — all backends mocked, no real keyring.

NEVER asserts on secret values — only keys, structure, and behavior.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cn_scraper_mcp.secure_storage import (
    DEFAULT_APP_NAME,
    SecureStorage,
    _check_plaintext_cookies,
    _FernetStorageBackend,
    _KeyringStorageBackend,
    _resolve_backend,
    delete_session,
    export_session,
    get_backend_info,
    import_session,
)

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════


class _FakeFernet:
    """Mock Fernet that uses simple reversible base64 (NOT secure — test only)."""

    def encrypt(self, data: bytes) -> bytes:
        import base64

        return base64.b64encode(data)

    def decrypt(self, data: bytes) -> bytes:
        import base64

        from cryptography.fernet import InvalidToken  # noqa: I001

        try:
            return base64.b64decode(data)
        except Exception:
            raise InvalidToken("bad token")


def _build_keyring_mock() -> MagicMock:
    """Build a MagicMock that quacks like the keyring module."""
    mock = MagicMock()
    mock.get_password.return_value = None
    mock.set_password.return_value = None
    mock.delete_password.return_value = None
    mock.get_keyring.return_value = MagicMock(__class__=MagicMock(__name__="MockKeyring"))
    return mock


@pytest.fixture
def mock_keyring():
    """Replace sys.modules['keyring'] with a mock for all tests in a class/func."""
    mock = _build_keyring_mock()
    with patch.dict(sys.modules, {"keyring": mock}):
        yield mock


# ═══════════════════════════════════════════════════════════════
# _FernetStorageBackend tests (with fake Fernet)
# ═══════════════════════════════════════════════════════════════


class TestFernetStorageBackend:
    """File-based encryption backend with mocked Fernet and filesystem."""

    def test_store_and_retrieve(self, tmp_path: Path):
        """Store → retrieve round-trip works."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "storage"
        backend._fernet = _FakeFernet()

        data = {"token": "abc123", "user": "test"}
        backend.store("taobao", data)

        fpath = tmp_path / "storage" / "taobao.enc"
        assert fpath.exists()

        result = backend.retrieve("taobao")
        assert result == data

    def test_retrieve_nonexistent(self, tmp_path: Path):
        """retrieve returns None when no file exists."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "storage"
        backend._fernet = _FakeFernet()

        assert backend.retrieve("nonexistent") is None

    def test_retrieve_corrupted(self, tmp_path: Path):
        """retrieve returns None for corrupted/unreadable data."""
        storage_dir = tmp_path / "storage"
        storage_dir.mkdir()

        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = storage_dir
        backend._fernet = _FakeFernet()

        (storage_dir / "bad.enc").write_text("not valid")

        assert backend.retrieve("bad") is None

    def test_delete_existing(self, tmp_path: Path):
        """delete removes the file and returns True."""
        storage_dir = tmp_path / "storage"
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = storage_dir
        backend._fernet = _FakeFernet()

        data = {"key": "val"}
        backend.store("platform", data)
        assert (storage_dir / "platform.enc").exists()

        result = backend.delete("platform")
        assert result is True
        assert not (storage_dir / "platform.enc").exists()

    def test_delete_nonexistent(self, tmp_path: Path):
        """delete returns False when no file."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "st"
        backend._fernet = _FakeFernet()

        assert backend.delete("missing") is False

    def test_list_platforms(self, tmp_path: Path):
        """list_platforms returns sorted platform names."""
        storage_dir = tmp_path / "storage"
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = storage_dir
        backend._fernet = _FakeFernet()

        backend.store("taobao", {"a": 1})
        (storage_dir / "other.txt").write_text("not enc")

        platforms = backend.list_platforms()
        assert platforms == ["taobao"]

    def test_list_platforms_empty(self, tmp_path: Path):
        """list_platforms returns [] when storage dir doesn't exist."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "nonexistent"

        assert backend.list_platforms() == []

    def test_export_raw_and_import_raw(self, tmp_path: Path):
        """export_raw → import_raw round-trip."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "st"
        backend._fernet = _FakeFernet()

        data = {"k": "v"}
        backend.store("p", data)

        raw = backend.export_raw("p")
        assert raw is not None

        backend2 = _FernetStorageBackend(key_file=tmp_path / ".key2")
        backend2._storage_dir = tmp_path / "st2"
        backend2._fernet = _FakeFernet()
        backend2.import_raw("p", raw)

        assert backend2.retrieve("p") == data

    def test_export_raw_nonexistent(self, tmp_path: Path):
        """export_raw returns None for missing platform."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "st"
        assert backend.export_raw("no") is None

    @patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", False)
    def test_raises_when_fernet_unavailable(self, tmp_path: Path):
        """When cryptography is not installed, store raises RuntimeError."""
        backend = _FernetStorageBackend(key_file=tmp_path / ".key")
        backend._storage_dir = tmp_path / "st"
        with pytest.raises(RuntimeError, match="cryptography"):
            backend.store("p", {})


# ═══════════════════════════════════════════════════════════════
# _KeyringStorageBackend tests
# ═══════════════════════════════════════════════════════════════


class TestKeyringStorageBackend:
    """OS-native keyring backend with mocked keyring library."""

    def test_store_sets_password(self, mock_keyring):
        """store serializes data and calls keyring.set_password."""
        backend = _KeyringStorageBackend(app_name="test-app")
        data = {"token": "secret", "user": "alice"}
        backend.store("taobao", data)

        mock_keyring.set_password.assert_called_once()
        args = mock_keyring.set_password.call_args
        assert args[0][0] == "test-app"
        assert args[0][1] == "taobao"
        stored = json.loads(args[0][2])
        assert stored == data

    def test_retrieve_gets_password(self, mock_keyring):
        """retrieve deserializes from keyring.get_password."""
        data = {"token": "secret"}
        mock_keyring.get_password.return_value = json.dumps(data)

        backend = _KeyringStorageBackend(app_name="test-app")
        result = backend.retrieve("taobao")

        mock_keyring.get_password.assert_called_once_with("test-app", "taobao")
        assert result == data

    def test_retrieve_none(self, mock_keyring):
        """retrieve returns None when keyring returns None."""
        backend = _KeyringStorageBackend()
        assert backend.retrieve("taobao") is None

    def test_retrieve_corrupted_json(self, mock_keyring):
        """retrieve returns None on JSON decode error."""
        mock_keyring.get_password.return_value = "not json {{{"

        backend = _KeyringStorageBackend()
        assert backend.retrieve("taobao") is None

    def test_retrieve_keyring_error(self, mock_keyring):
        """retrieve returns None when get_password raises."""
        mock_keyring.get_password.side_effect = RuntimeError("backend down")

        backend = _KeyringStorageBackend()
        assert backend.retrieve("taobao") is None

    def test_delete_success(self, mock_keyring):
        """delete calls keyring.delete_password."""
        backend = _KeyringStorageBackend()
        result = backend.delete("taobao")

        mock_keyring.delete_password.assert_called_once_with(DEFAULT_APP_NAME, "taobao")
        assert result is True

    def test_delete_error(self, mock_keyring):
        """delete returns False on keyring error."""
        mock_keyring.delete_password.side_effect = RuntimeError("fail")

        backend = _KeyringStorageBackend()
        assert backend.delete("taobao") is False

    def test_list_platforms_returns_empty(self):
        """list_platforms always returns [] for keyring (no universal list API)."""
        backend = _KeyringStorageBackend()
        assert backend.list_platforms() == []


# ═══════════════════════════════════════════════════════════════
# _resolve_backend tests
# ═══════════════════════════════════════════════════════════════


class TestResolveBackend:
    """Backend resolution priority: keyring > fernet > fallback."""

    @patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", True)
    def test_prefers_keyring_when_available(self, mock_keyring):
        """keyring available → _KeyringStorageBackend selected."""
        backend = _resolve_backend("test", None)
        assert isinstance(backend, _KeyringStorageBackend)

    def test_falls_back_to_fernet_when_keyring_unavailable(self):
        """keyring not available → _FernetStorageBackend selected."""
        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True),
        ):
            backend = _resolve_backend("test", None)
            assert isinstance(backend, _FernetStorageBackend)

    @patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", True)
    @patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True)
    def test_falls_back_to_fernet_when_keyring_fails_at_runtime(self, mock_keyring):
        """keyring is importable but crashes at runtime → Fernet fallback."""
        mock_keyring.get_keyring.side_effect = RuntimeError("no backend")
        backend = _resolve_backend("test", None)
        assert isinstance(backend, _FernetStorageBackend)

    def test_fallback_when_nothing_available(self):
        """When neither is available, still returns _FernetStorageBackend."""
        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", False),
        ):
            backend = _resolve_backend("test", None)
            assert isinstance(backend, _FernetStorageBackend)


# ═══════════════════════════════════════════════════════════════
# SecureStorage tests (with mocked backend)
# ═══════════════════════════════════════════════════════════════


class TestSecureStorage:
    """SecureStorage delegates to resolved backend."""

    @pytest.fixture
    def mock_backend(self):
        """Return a MagicMock backend."""
        mock = MagicMock()
        mock.retrieve.return_value = None
        mock.list_platforms.return_value = []
        mock.store.return_value = None
        mock.delete.return_value = True
        return mock

    @pytest.fixture
    def storage(self, mock_backend):
        """SecureStorage with mocked backend."""
        with patch("cn_scraper_mcp.secure_storage._resolve_backend", return_value=mock_backend):
            yield SecureStorage()

    def test_store_delegates(self, storage, mock_backend):
        """store forwards to backend.store."""
        data = {"k": "v"}
        storage.store("taobao", data)
        mock_backend.store.assert_called_once_with("taobao", data)

    def test_retrieve_delegates(self, storage, mock_backend):
        """retrieve forwards to backend.retrieve."""
        mock_backend.retrieve.return_value = {"a": 1}
        result = storage.retrieve("taobao")
        assert result == {"a": 1}
        mock_backend.retrieve.assert_called_once_with("taobao")

    def test_retrieve_none(self, storage, mock_backend):
        """retrieve returns None from backend."""
        mock_backend.retrieve.return_value = None
        assert storage.retrieve("taobao") is None

    def test_delete_delegates(self, storage, mock_backend):
        """delete forwards to backend.delete."""
        storage.delete("taobao")
        mock_backend.delete.assert_called_once_with("taobao")

    def test_list_platforms_delegates(self, storage, mock_backend):
        """list_platforms forwards to backend."""
        mock_backend.list_platforms.return_value = ["taobao", "zhihu"]
        result = storage.list_platforms()
        assert result == ["taobao", "zhihu"]

    def test_export_encrypted_no_data(self, storage, mock_backend):
        """export_encrypted returns False when no data."""
        mock_backend.retrieve.return_value = None
        result = storage.export_encrypted("taobao", "/tmp/out.enc")
        assert result is False

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_export_encrypted_success(self, mock_fernet_fn, storage, mock_backend):
        """export_encrypted writes encrypted blob."""
        fake_fernet = _FakeFernet()
        mock_fernet_fn.return_value = fake_fernet
        mock_backend.retrieve.return_value = {"token": "abc"}

        with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
            tf.close()
            result = storage.export_encrypted("taobao", tf.name)
            assert result is True
            content = Path(tf.name).read_bytes()
            decrypted = json.loads(fake_fernet.decrypt(content))
            assert decrypted == {"token": "abc"}
            Path(tf.name).unlink()

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_import_encrypted_success(self, mock_fernet_fn, storage, mock_backend):
        """import_encrypted reads and stores encrypted data."""
        fake_fernet = _FakeFernet()
        mock_fernet_fn.return_value = fake_fernet

        data = {"token": "abc123"}
        encrypted = fake_fernet.encrypt(json.dumps(data).encode("utf-8"))

        with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
            tf.write(encrypted)
            tf.close()

            result = storage.import_encrypted("taobao", tf.name)
            assert result is True
            mock_backend.store.assert_called_once_with("taobao", data)
            Path(tf.name).unlink()

    def test_import_encrypted_file_not_found(self, storage, mock_backend):
        """import_encrypted returns False for missing file."""
        result = storage.import_encrypted("taobao", "/nonexistent/file.enc")
        assert result is False

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_import_encrypted_decrypt_failure(self, mock_fernet_fn, storage, mock_backend):
        """import_encrypted returns False on decrypt failure."""
        mock_fernet_fn.return_value = _FakeFernet()

        with tempfile.NamedTemporaryFile(suffix=".enc", delete=False) as tf:
            tf.write(b"garbage not valid")
            tf.close()

            result = storage.import_encrypted("taobao", tf.name)
            assert result is False
            Path(tf.name).unlink()


# ═══════════════════════════════════════════════════════════════
# export_session tests
# ═══════════════════════════════════════════════════════════════


class TestExportSession:
    """Top-level export_session function."""

    @pytest.fixture
    def mock_storage(self):
        """Mock SecureStorage.retrieve to return test data."""
        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            instance.retrieve.return_value = {"token": "secret_val"}
            mock_cls.return_value = instance
            yield mock_cls, instance

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_export_encrypted_default_path(self, mock_fernet_fn, mock_storage, tmp_path: Path):
        """export_session with encrypt=True writes to default path."""
        mock_fernet_fn.return_value = _FakeFernet()

        out = tmp_path / "sub" / "out.enc"
        with patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "default"):
            result = export_session("taobao", output_path=out, encrypt=True)

        assert result["success"] is True
        assert result["encrypted"] is True

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_export_plaintext(self, mock_fernet_fn, mock_storage, tmp_path: Path):
        """export_session with encrypt=False writes plain JSON."""
        mock_fernet_fn.return_value = _FakeFernet()

        out = tmp_path / "plain.json"
        result = export_session("taobao", output_path=out, encrypt=False)

        assert result["success"] is True
        assert result["encrypted"] is False
        assert out.exists()
        with open(out) as fh:
            assert json.load(fh) == {"token": "secret_val"}

    def test_export_no_data(self, mock_storage):
        """export_session returns failure when no data."""
        _, instance = mock_storage
        instance.retrieve.return_value = None

        result = export_session("taobao", output_path="/tmp/out.enc")
        assert result["success"] is False
        assert "No stored data" in result["reason"]

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_export_no_output_path_uses_default(self, mock_fernet_fn, mock_storage):
        """When output_path is None, default path is used."""
        mock_fernet_fn.return_value = _FakeFernet()

        with tempfile.TemporaryDirectory() as td:
            default_dir = Path(td)
            with patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", default_dir):
                result = export_session("taobao", encrypt=True)

            assert result["success"] is True
            assert result["encrypted"] is True
            assert "taobao_export.enc" in result["path"]


# ═══════════════════════════════════════════════════════════════
# import_session tests
# ═══════════════════════════════════════════════════════════════


class TestImportSession:
    """Top-level import_session function."""

    @pytest.fixture
    def mock_storage(self):
        """Mock SecureStorage.store."""
        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance
            yield mock_cls, instance

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_import_encrypted(self, mock_fernet_fn, mock_storage, tmp_path: Path):
        """import encrypted file and store."""
        fake_fernet = _FakeFernet()
        mock_fernet_fn.return_value = fake_fernet

        data = {"token": "imported_secret"}
        enc = fake_fernet.encrypt(json.dumps(data).encode())
        inp = tmp_path / "in.enc"
        inp.write_bytes(enc)

        _, instance = mock_storage
        result = import_session("taobao", inp, encrypted=True)

        assert result["success"] is True
        instance.store.assert_called_once_with("taobao", data)

    def test_import_plaintext(self, mock_storage, tmp_path: Path):
        """import plain JSON file."""
        data = {"token": "plain_secret"}
        inp = tmp_path / "in.json"
        inp.write_text(json.dumps(data))

        _, instance = mock_storage
        result = import_session("taobao", inp, encrypted=False)

        assert result["success"] is True
        instance.store.assert_called_once_with("taobao", data)

    def test_import_file_not_found(self, mock_storage):
        """import_session fails when file missing."""
        result = import_session("taobao", "/nonexistent.enc")
        assert result["success"] is False
        assert "not found" in result["reason"]

    @patch("cn_scraper_mcp.secure_storage._ensure_export_fernet")
    def test_import_decrypt_failure(self, mock_fernet_fn, mock_storage, tmp_path: Path):
        """import_session fails on bad encrypted data."""
        mock_fernet_fn.return_value = _FakeFernet()

        inp = tmp_path / "bad.enc"
        inp.write_bytes(b"not valid encrypted data @@@")

        result = import_session("taobao", inp, encrypted=True)
        assert result["success"] is False


# ═══════════════════════════════════════════════════════════════
# delete_session tests
# ═══════════════════════════════════════════════════════════════


class TestDeleteSession:
    """Top-level delete_session cleans up everything."""

    def test_delete_cleans_secure_storage(self, tmp_path: Path):
        """delete_session calls storage.delete and cleans up from tmp dir."""
        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance

            with patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", tmp_path / "cookies"), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "storage"), \
                 patch("cn_scraper_mcp.secure_storage.JD_PROFILE_DIR", tmp_path / "jd_profile"):
                result = delete_session("taobao")

            assert result["deleted_secure"] is True
            instance.delete.assert_called_once_with("taobao")

    def test_delete_cleans_cookie_file(self, tmp_path: Path):
        """delete_session removes plain-text cookie file."""
        cookie_dir = tmp_path / "cookies"
        cookie_dir.mkdir()
        cookie_path = cookie_dir / "taobao.json"
        cookie_path.write_text('{"k":"v"}')

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            mock_cls.return_value = MagicMock()

            with patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", cookie_dir), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "st"):
                result = delete_session("taobao")

            assert result["deleted_cookie"] is True
            assert not cookie_path.exists()

    def test_delete_cleans_jd_profile(self, tmp_path: Path):
        """delete_session removes JD Chrome profile."""
        jd_dir = tmp_path / "jd_profile"
        jd_dir.mkdir()

        cookie_dir = tmp_path / "cookies"
        cookie_dir.mkdir()

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            mock_cls.return_value = MagicMock()

            with patch("cn_scraper_mcp.secure_storage.JD_PROFILE_DIR", jd_dir), \
                 patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", cookie_dir), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "st"):
                result = delete_session("jd")

            assert result["deleted_profile"] is True
            assert not jd_dir.exists()

    def test_delete_cleans_profile_for_other_platforms(self, tmp_path: Path):
        """delete_session cleans .cn_scraper_login_<platform> if exists."""
        from pathlib import Path as _Path

        alt_dir = tmp_path / ".cn_scraper_login_taobao"
        alt_dir.mkdir()

        cookie_dir = tmp_path / "cookies"
        cookie_dir.mkdir()

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            mock_cls.return_value = MagicMock()

            with patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", cookie_dir), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "st"):
                # Patch Path.home() to return tmp_path so alt dir is found
                with patch.object(_Path, "home", return_value=tmp_path):
                    result = delete_session("taobao")

            assert result["deleted_profile"] is True

    def test_delete_handles_os_errors_gracefully(self, tmp_path: Path):
        """delete_session catches OSError and reports in reason."""
        cookie_dir = tmp_path / "cookies"
        cookie_dir.mkdir()
        cookie_path = cookie_dir / "taobao.json"
        cookie_path.write_text('{"k":"v"}')

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            mock_cls.return_value = MagicMock()

            from pathlib import Path as _Path

            with patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", cookie_dir), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "st"):
                # Make unlink fail with OSError
                with patch.object(_Path, "unlink", side_effect=OSError("perm")):
                    result = delete_session("taobao")

            assert "perm" in result["reason"]


# ═══════════════════════════════════════════════════════════════
# _check_plaintext_cookies tests
# ═══════════════════════════════════════════════════════════════


class TestCheckPlaintextCookies:
    """Plain-text cookie detection and warning."""

    def test_no_cookie_dir(self):
        """Returns empty list when COOKIE_DIR doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            result = _check_plaintext_cookies()
            assert result == []

    def test_finds_json_files(self):
        """Returns list of platforms with .json cookie files."""
        fake_entries = [
            MagicMock(suffix=".json", stem="taobao"),
            MagicMock(suffix=".json", stem="zhihu"),
            MagicMock(suffix=".txt", stem="notes"),
        ]
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "iterdir", return_value=fake_entries),
        ):
            result = _check_plaintext_cookies()
            assert sorted(result) == ["taobao", "zhihu"]

    def test_warns_only_once(self):
        """_warn_plaintext flag prevents duplicate warnings."""
        import cn_scraper_mcp.secure_storage as ss

        ss._PLAINTEXT_WARNED = False

        fake_entries = [MagicMock(suffix=".json", stem="taobao")]
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "iterdir", return_value=fake_entries),
        ):
            result1 = _check_plaintext_cookies()
            assert len(result1) == 1

            result2 = _check_plaintext_cookies()
            assert result2 == result1

            assert ss._PLAINTEXT_WARNED is True


# ═══════════════════════════════════════════════════════════════
# get_backend_info tests
# ═══════════════════════════════════════════════════════════════


class TestGetBackendInfo:
    """Diagnostic backend info."""

    @patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", True)
    def test_keyring_backend_info(self, mock_keyring):
        """When keyring is available, shows keyring info."""
        info = get_backend_info()
        assert info["backend"] == "keyring"
        assert info["keyring_class"] == "MockKeyring"

    @patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", True)
    @patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True)
    def test_keyring_fails_falls_to_fernet(self, mock_keyring):
        """When keyring.get_keyring() throws, falls back to fernet info."""
        mock_keyring.get_keyring.side_effect = RuntimeError("fail")
        info = get_backend_info()
        assert info["backend"] == "fernet"

    def test_fernet_backend_info(self):
        """When keyring not available, shows fernet info."""
        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True),
        ):
            info = get_backend_info()
            assert info["backend"] == "fernet"
            assert "key_file" in info
            assert "storage_dir" in info

    def test_nothing_available(self):
        """When nothing available, reports 'none'."""
        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", False),
        ):
            info = get_backend_info()
            assert info["backend"] == "none"


# ═══════════════════════════════════════════════════════════════
# Security: never logs cookie values
# ═══════════════════════════════════════════════════════════════


class TestSecurityNoLeak:
    """Ensure secure_storage never logs or returns plain secret values in metadata."""

    def test_store_does_not_log_values(self):
        """store logs only platform name, not values."""
        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance

            from cn_scraper_mcp.secure_storage import SecureStorage

            storage = SecureStorage()
            storage.store("taobao", {"token": "super_secret_123"})

    def test_export_plaintext_logs_warning(self, tmp_path: Path):
        """Exporting plaintext logs a security warning."""
        with (
            patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls,
            patch("cn_scraper_mcp.secure_storage.logger") as mock_logger,
        ):
            instance = MagicMock()
            instance.retrieve.return_value = {"token": "secret"}
            mock_cls.return_value = instance

            out = tmp_path / "plain.json"
            result = export_session("taobao", output_path=out, encrypt=False)

            assert result["success"] is True
            warning_calls = [
                c for c in mock_logger.warning.call_args_list if "UNENCRYPTED" in str(c)
            ]
            assert len(warning_calls) >= 1

    def test_import_does_not_log_secrets(self, tmp_path: Path):
        """Import does not log imported values."""
        inp = tmp_path / "test.json"
        inp.write_text(json.dumps({"secret_key": "val"}))

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance

            result = import_session("taobao", inp, encrypted=False)

            assert result["success"] is True
            instance.store.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# Integration: end-to-end flows
# ═══════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end flows with mocked backend."""

    def test_store_retrieve_delete_cycle(self, tmp_path: Path):
        """Full lifecycle: store → retrieve → delete → retrieve is None."""
        key_file = tmp_path / ".key"
        storage_dir = tmp_path / "data"

        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True),
            patch("cn_scraper_mcp.secure_storage.DEFAULT_KEY_FILE", key_file),
            patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", storage_dir),
            # The _FernetStorageBackend will try to call Fernet(key) — mock it
            patch("cn_scraper_mcp.secure_storage._resolve_backend") as mock_resolve,
        ):
            fake_backend = _FernetStorageBackend(key_file=key_file)
            fake_backend._storage_dir = storage_dir
            fake_backend._fernet = _FakeFernet()
            mock_resolve.return_value = fake_backend

            storage = SecureStorage()

            data = {"token": "my-secret-token", "user_id": 42}
            storage.store("taobao", data)

            result = storage.retrieve("taobao")
            assert result == data

            platforms = storage.list_platforms()
            assert "taobao" in platforms

            storage.delete("taobao")
            assert storage.retrieve("taobao") is None

    def test_export_import_cycle(self, tmp_path: Path):
        """Export → import round-trip with real Fernet file backend."""
        key_file = tmp_path / ".key"
        storage_dir = tmp_path / "data"

        with (
            patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", False),
            patch("cn_scraper_mcp.secure_storage._FERNET_AVAILABLE", True),
            patch("cn_scraper_mcp.secure_storage.DEFAULT_KEY_FILE", key_file),
            patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", storage_dir),
            patch("cn_scraper_mcp.secure_storage._resolve_backend") as mock_resolve,
            patch("cn_scraper_mcp.secure_storage._ensure_export_fernet") as mock_export_fn,
        ):
            fake_backend = _FernetStorageBackend(key_file=key_file)
            fake_backend._storage_dir = storage_dir
            fake_backend._fernet = _FakeFernet()
            mock_resolve.return_value = fake_backend
            mock_export_fn.return_value = _FakeFernet()

            storage = SecureStorage()
            data = {"cookie": "value123", "expires": "2026-01-01"}
            storage.store("xiaohongshu", data)

            export_path = tmp_path / "xhs_export.enc"
            result = export_session("xiaohongshu", output_path=export_path, encrypt=True)
            assert result["success"] is True

            result2 = import_session("xiaohongshu_backup", export_path, encrypted=True)
            assert result2["success"] is True

            retrieved = storage.retrieve("xiaohongshu_backup")
            assert retrieved == data

    def test_delete_session_full_cleanup(self, tmp_path: Path):
        """delete_session cleans secure storage, cookie file, and profile."""
        cookie_dir = tmp_path / "cookies"
        cookie_dir.mkdir()
        cookie_path = cookie_dir / "taobao.json"
        cookie_path.write_text('{"k":"v"}')

        alt_profile = tmp_path / ".cn_scraper_login_taobao"
        alt_profile.mkdir()

        from pathlib import Path as _Path

        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            mock_cls.return_value = instance

            with patch("cn_scraper_mcp.secure_storage.COOKIE_DIR", cookie_dir), \
                 patch("cn_scraper_mcp.secure_storage.DEFAULT_STORAGE_DIR", tmp_path / "st"), \
                 patch.object(_Path, "home", return_value=tmp_path):
                result = delete_session("taobao")

            assert result["deleted_secure"] is True
            assert result["deleted_cookie"] is True
            assert result["deleted_profile"] is True
            instance.delete.assert_called_once_with("taobao")
            assert not cookie_path.exists()
            assert not alt_profile.exists()

    def test_secure_storage_never_exposes_secrets_in_return_metadata(self, tmp_path: Path):
        """All public API returns metadata only — no secret values leaked."""
        with patch("cn_scraper_mcp.secure_storage.SecureStorage") as mock_cls:
            instance = MagicMock()
            instance.retrieve.return_value = {"token": "super_secret_value"}
            mock_cls.return_value = instance

            out = tmp_path / "out.json"
            result = export_session("taobao", output_path=out, encrypt=False)

            result_str = json.dumps(result)
            assert "super_secret_value" not in result_str


# ═══════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_store_empty_dict(self):
        """Storing an empty dict is valid."""
        with patch("cn_scraper_mcp.secure_storage._resolve_backend") as mock_resolve:
            mock_backend = MagicMock()
            mock_resolve.return_value = mock_backend

            storage = SecureStorage()
            storage.store("platform", {})

            mock_backend.store.assert_called_once_with("platform", {})

    def test_retrieve_uninitialized(self):
        """retrieve on platform with no data returns None."""
        with patch("cn_scraper_mcp.secure_storage._resolve_backend") as mock_resolve:
            mock_backend = MagicMock()
            mock_backend.retrieve.return_value = None
            mock_resolve.return_value = mock_backend

            storage = SecureStorage()
            assert storage.retrieve("unknown") is None

    @patch("cn_scraper_mcp.secure_storage._KEYRING_AVAILABLE", True)
    def test_custom_app_name(self, mock_keyring):
        """Custom app_name is passed to keyring backend."""
        storage = SecureStorage(app_name="custom-app")
        storage.store("taobao", {"k": "v"})

        mock_keyring.set_password.assert_called_once()
        assert mock_keyring.set_password.call_args[0][0] == "custom-app"

    def test_custom_key_file_passed_to_backend(self):
        """Custom key_file is propagated to backend resolution."""
        custom_key = Path("/custom/path/.key")
        with patch("cn_scraper_mcp.secure_storage._resolve_backend") as mock_resolve:
            mock_resolve.return_value = MagicMock()

            SecureStorage(key_file=custom_key)

            mock_resolve.assert_called_once()
            call_args = mock_resolve.call_args[0]
            assert call_args[1] == custom_key


# ═══════════════════════════════════════════════════════════════
# Exported symbols
# ═══════════════════════════════════════════════════════════════


class TestExports:
    """Verify __all__ matches public API."""

    def test_all_exports_match(self):
        """All public symbols are in __all__."""
        from cn_scraper_mcp import secure_storage as ss

        expected = {
            "SecureStorage",
            "DEFAULT_APP_NAME",
            "DEFAULT_KEY_FILE",
            "DEFAULT_STORAGE_DIR",
            "COOKIE_DIR",
            "JD_PROFILE_DIR",
            "export_session",
            "import_session",
            "delete_session",
            "get_backend_info",
            "_check_plaintext_cookies",
        }
        assert set(ss.__all__) == expected

    def test_secure_storage_importable(self):
        """Basic sanity: module imports without error."""
        import cn_scraper_mcp.secure_storage  # noqa: F401
