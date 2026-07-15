"""Secure credential storage for cn-scraper-mcp.

Multi-platform backend chain (highest priority first):
  - Windows: Credential Manager (via keyring)
  - macOS:   Keychain (via keyring)
  - Linux:   Secret Service / D-Bus (via keyring)
  - Fallback: Fernet symmetric encryption + file (~/.cn-scraper-mcp/)

Plain-text cookie files (~/.cn-scraper-cookies/) remain readable as a
backward-compatible fallback, but a security warning is logged at startup.

Usage::

    from cn_scraper_mcp.secure_storage import SecureStorage

    storage = SecureStorage()
    storage.store("taobao", {"_m_h5_tk": "...", "_tb_token_": "..."})
    data = storage.retrieve("taobao")  # → dict or None
    storage.delete("taobao")
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from cn_scraper_mcp.logging import get_logger

logger = get_logger("cn_scraper_mcp.secure_storage")

# ═══════════════════════════════════════════════════════════════
# Backend discovery
# ═══════════════════════════════════════════════════════════════

_KEYRING_AVAILABLE = False
_FERNET_AVAILABLE = False

try:
    import keyring  # noqa: F401

    _KEYRING_AVAILABLE = True
except ImportError:
    pass

try:
    from cryptography.fernet import Fernet, InvalidToken  # noqa: F401

    _FERNET_AVAILABLE = True
except ImportError:
    pass

# ═══════════════════════════════════════════════════════════════
# Default paths
# ═══════════════════════════════════════════════════════════════

DEFAULT_APP_NAME: str = "cn-scraper-mcp"
DEFAULT_KEY_FILE: Path = Path.home() / ".cn-scraper-mcp.key"
DEFAULT_STORAGE_DIR: Path = Path.home() / ".cn-scraper-mcp"
COOKIE_DIR: Path = Path.home() / ".cn-scraper-cookies"
JD_PROFILE_DIR: Path = Path.home() / ".jd_login_profile"

# ═══════════════════════════════════════════════════════════════
# Security warning for plain-text cookies
# ═══════════════════════════════════════════════════════════════

_PLAINTEXT_WARNED = False


def _warn_plaintext() -> None:
    """Emit a one-time security warning about plain-text cookie files."""
    global _PLAINTEXT_WARNED
    if not _PLAINTEXT_WARNED:
        logger.warning(
            "Plain-text cookie files detected in %s. "
            "Consider using SecureStorage for encrypted storage.",
            COOKIE_DIR,
        )
        _PLAINTEXT_WARNED = True


# ═══════════════════════════════════════════════════════════════
# Fernet file backend (fallback)
# ═══════════════════════════════════════════════════════════════


class _FernetStorageBackend:
    """File-based encrypted storage using Fernet symmetric encryption.

    Stores each platform's data as a separate .enc file under
    ~/.cn-scraper-mcp/<platform>.enc.  The Fernet key is read from
    *key_file* (default: ~/.cn-scraper-mcp.key).  If the key file does
    not exist, a new key is generated and persisted.
    """

    def __init__(self, key_file: Path | None = None) -> None:
        self._key_file = key_file or DEFAULT_KEY_FILE
        self._storage_dir = DEFAULT_STORAGE_DIR
        self._fernet: Any = None  # Fernet instance, lazy-init

    def _ensure_key(self) -> Any:
        """Return a Fernet instance, creating a key file if needed."""
        if self._fernet is not None:
            return self._fernet

        if not _FERNET_AVAILABLE:
            raise RuntimeError(
                "cryptography library is not installed. Install it with: pip install cryptography"
            )

        from cryptography.fernet import Fernet as _Fernet

        if self._key_file.exists():
            key = self._key_file.read_bytes()
        else:
            key = _Fernet.generate_key()
            self._storage_dir.mkdir(parents=True, exist_ok=True)
            self._key_file.write_bytes(key)
            # Restrictive permissions on POSIX
            if os.name == "posix":
                self._key_file.chmod(0o600)

        self._fernet = _Fernet(key)
        return self._fernet

    def _data_path(self, platform: str) -> Path:
        """Return the file path for a platform's encrypted data."""
        return self._storage_dir / f"{platform}.enc"

    def store(self, platform: str, data: dict) -> None:
        """Encrypt and persist *data* for *platform*."""
        f = self._ensure_key()
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        encrypted = f.encrypt(payload)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._data_path(platform).write_bytes(encrypted)
        logger.debug("Stored encrypted session for platform=%s", platform)

    def retrieve(self, platform: str) -> dict | None:
        """Decrypt and return stored data, or None."""
        path = self._data_path(platform)
        if not path.exists():
            return None
        try:
            f = self._ensure_key()
            encrypted = path.read_bytes()
            payload = f.decrypt(encrypted)
            return json.loads(payload.decode("utf-8"))
        except Exception:
            logger.debug("Failed to decrypt/parse session for platform=%s", platform)
            return None

    def delete(self, platform: str) -> bool:
        """Remove the encrypted file for *platform*.  Returns True on success."""
        path = self._data_path(platform)
        if path.exists():
            try:
                path.unlink()
                logger.debug("Deleted encrypted session for platform=%s", platform)
                return True
            except OSError:
                return False
        return False

    def list_platforms(self) -> list[str]:
        """Return all platform names with stored encrypted data."""
        if not self._storage_dir.exists():
            return []
        platforms: list[str] = []
        for entry in self._storage_dir.iterdir():
            if entry.suffix == ".enc":
                platforms.append(entry.stem)
        return sorted(platforms)

    def export_raw(self, platform: str) -> bytes | None:
        """Export the raw encrypted bytes for *platform*."""
        path = self._data_path(platform)
        if path.exists():
            return path.read_bytes()
        return None

    def import_raw(self, platform: str, data: bytes) -> None:
        """Import raw encrypted bytes for *platform*."""
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._data_path(platform).write_bytes(data)


# ═══════════════════════════════════════════════════════════════
# keyring backend
# ═══════════════════════════════════════════════════════════════


class _KeyringStorageBackend:
    """OS-native credential storage via the keyring library.

    Uses service name = app_name, username = platform.
    Stores JSON-serialized data as the password.
    """

    def __init__(self, app_name: str = DEFAULT_APP_NAME) -> None:
        self._app_name = app_name

    def store(self, platform: str, data: dict) -> None:
        """Store *data* in the OS credential store."""
        import keyring

        payload = json.dumps(data, ensure_ascii=False)
        keyring.set_password(self._app_name, platform, payload)
        logger.debug("Stored keyring session for platform=%s", platform)

    def retrieve(self, platform: str) -> dict | None:
        """Retrieve and parse stored data, or None."""
        import keyring

        try:
            raw = keyring.get_password(self._app_name, platform)
        except Exception:
            logger.debug("keyring.get_password failed for platform=%s", platform)
            return None

        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Corrupted keyring data for platform=%s", platform)
            return None

    def delete(self, platform: str) -> bool:
        """Remove the credential for *platform*."""
        import keyring

        try:
            keyring.delete_password(self._app_name, platform)
            logger.debug("Deleted keyring session for platform=%s", platform)
            return True
        except Exception:
            return False

    def list_platforms(self) -> list[str]:
        """Return all platforms stored in the keyring.

        Note: keyring does not natively support listing credentials
        across all backends.  This returns an empty list when listing
        is not feasible (always on non-macOS keyring backends).
        """
        # The keyring library has no universal "list credentials" API.
        # We *could* use keyring.get_credential() per-platform, but we
        # don't know which platforms exist.  For now, return empty.
        return []


# ═══════════════════════════════════════════════════════════════
# Backend resolver
# ═══════════════════════════════════════════════════════════════


def _resolve_backend(app_name: str, key_file: Path | None) -> Any:
    """Return the best available storage backend instance.

    Priority: keyring > Fernet file.
    """
    if _KEYRING_AVAILABLE:
        try:
            backend = _KeyringStorageBackend(app_name=app_name)
            # Quick smoke test — some keyring backends fail at runtime
            import keyring

            backend_name = keyring.get_keyring().__class__.__name__
            logger.debug("Using keyring backend: %s", backend_name)
            return backend
        except Exception as exc:
            logger.debug("keyring backend unavailable (%s), falling back to Fernet", exc)

    if _FERNET_AVAILABLE:
        logger.debug("Using Fernet file backend (key=%s)", key_file or DEFAULT_KEY_FILE)
        return _FernetStorageBackend(key_file=key_file)

    # Last resort: Fernet-based via pure Python base64 (not real encryption,
    # but the module remains functional).  The _FernetStorageBackend will
    # raise RuntimeError on first use if cryptography is missing.
    logger.warning(
        "No secure storage backend available (install keyring or cryptography). "
        "Falling back to Fernet file backend — will fail if cryptography is missing."
    )
    return _FernetStorageBackend(key_file=key_file)


# ═══════════════════════════════════════════════════════════════
# SecureStorage — public API
# ═══════════════════════════════════════════════════════════════


class SecureStorage:
    """Multi-platform secure credential storage.

    Automatically selects the best available backend:
      - keyring (Windows Credential Manager / macOS Keychain / Linux Secret Service)
      - Fernet file-based encryption (fallback)

    Usage::

        storage = SecureStorage()
        storage.store("taobao", {"_m_h5_tk": "...", "cookie2": "..."})
        data = storage.retrieve("taobao")
        storage.delete("taobao")
    """

    def __init__(
        self,
        app_name: str = DEFAULT_APP_NAME,
        key_file: Path | str | None = None,
    ) -> None:
        self._app_name = app_name
        self._key_file = Path(key_file) if key_file else None
        self._backend = _resolve_backend(app_name, self._key_file)
        self._is_fernet = isinstance(self._backend, _FernetStorageBackend)

    # ── core operations ─────────────────────────────────

    def store(self, platform: str, data: dict) -> None:
        """Securely store session data for *platform*.

        Args:
            platform: Platform identifier (e.g. "taobao", "xiaohongshu").
            data: Dictionary of credential data to store.
        """
        self._backend.store(platform, data)

    def retrieve(self, platform: str) -> dict | None:
        """Retrieve stored session data for *platform*, or None."""
        return self._backend.retrieve(platform)

    def delete(self, platform: str) -> None:
        """Delete stored session data for *platform*."""
        self._backend.delete(platform)

    def list_platforms(self) -> list[str]:
        """Return all platforms that have stored credentials."""
        return self._backend.list_platforms()

    # ── export / import ─────────────────────────────────

    def export_encrypted(self, platform: str, output_path: Path | str) -> bool:
        """Export an encrypted blob for *platform* to *output_path*.

        The output file contains the raw encrypted bytes — it can only
        be decrypted with the same Fernet key (or keyring backend).

        Returns True on success, False if no data exists.
        """
        data = self.retrieve(platform)
        if data is None:
            logger.warning("No stored data to export for platform=%s", platform)
            return False

        # For keyring backend: re-encrypt with Fernet for portable export
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        f = _ensure_export_fernet(self._key_file)
        encrypted = f.encrypt(payload)
        Path(output_path).write_bytes(encrypted)
        logger.info("Exported encrypted session for %s → %s", platform, output_path)
        return True

    def import_encrypted(self, platform: str, input_path: Path | str) -> bool:
        """Import an encrypted blob from *input_path* for *platform*.

        Returns True on success.
        """
        path = Path(input_path)
        if not path.exists():
            logger.warning("Import file not found: %s", input_path)
            return False

        try:
            encrypted = path.read_bytes()
            f = _ensure_export_fernet(self._key_file)
            payload = f.decrypt(encrypted)
            data = json.loads(payload.decode("utf-8"))
            self.store(platform, data)
            logger.info("Imported encrypted session for %s ← %s", platform, input_path)
            return True
        except Exception as exc:
            logger.error("Failed to import session for %s: %s", platform, exc)
            return False


# ── export Fernet helper (shared) ──────────────────────────

_export_fernet: Any = None
_export_key_file: Path | None = None


def _ensure_export_fernet(key_file: Path | None) -> Any:
    """Return a Fernet instance for export/import, creating key if needed."""
    global _export_fernet, _export_key_file

    kf = key_file or DEFAULT_KEY_FILE
    if _export_fernet is not None and _export_key_file == kf:
        return _export_fernet

    if not _FERNET_AVAILABLE:
        raise RuntimeError(
            "cryptography is required for export/import. Install it with: pip install cryptography"
        )

    from cryptography.fernet import Fernet as _Fernet

    if kf.exists():
        key = kf.read_bytes()
    else:
        key = _Fernet.generate_key()
        kf.parent.mkdir(parents=True, exist_ok=True)
        kf.write_bytes(key)
        if os.name == "posix":
            kf.chmod(0o600)

    _export_fernet = _Fernet(key)
    _export_key_file = kf
    return _export_fernet


# ═══════════════════════════════════════════════════════════════
# Top-level session helpers — integrate with SessionManager
# ═══════════════════════════════════════════════════════════════


def export_session(
    platform: str,
    output_path: Path | str | None = None,
    encrypt: bool = True,
) -> dict:
    """Export session data for *platform* to a file.

    Args:
        platform: Platform identifier.
        output_path: Target file path.  Defaults to
            ~/.cn-scraper-mcp/<platform>_export.enc (or .json if not encrypted).
        encrypt: If True (default), encrypt with Fernet.  If False, write
            plain JSON (dangerous — logs a warning).

    Returns:
        {"success": bool, "path": str, "encrypted": bool, "reason": str}
    """
    storage = SecureStorage()
    data = storage.retrieve(platform)

    if data is None:
        return {
            "success": False,
            "path": "",
            "encrypted": encrypt,
            "reason": f"No stored data found for platform '{platform}'",
        }

    if output_path is None:
        ext = ".enc" if encrypt else ".json"
        output_path = DEFAULT_STORAGE_DIR / f"{platform}_export{ext}"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        if encrypt:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            f = _ensure_export_fernet(None)
            encrypted = f.encrypt(payload)
            out.write_bytes(encrypted)
        else:
            logger.warning(
                "Exporting UNENCRYPTED session data for %s to %s. This is a security risk.",
                platform,
                out,
            )
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        return {"success": True, "path": str(out), "encrypted": encrypt, "reason": ""}
    except Exception as exc:
        return {
            "success": False,
            "path": str(out),
            "encrypted": encrypt,
            "reason": str(exc),
        }


def import_session(
    platform: str,
    input_path: Path | str,
    encrypted: bool = True,
) -> dict:
    """Import session data for *platform* from a file.

    Args:
        platform: Platform identifier.
        input_path: Source file path.
        encrypted: If True (default), decrypt with Fernet before storing.
            If False, read as plain JSON.

    Returns:
        {"success": bool, "path": str, "reason": str}
    """
    inp = Path(input_path)
    if not inp.exists():
        return {
            "success": False,
            "path": str(inp),
            "reason": f"Input file not found: {inp}",
        }

    try:
        if encrypted:
            raw = inp.read_bytes()
            f = _ensure_export_fernet(None)
            payload = f.decrypt(raw)
            data = json.loads(payload.decode("utf-8"))
        else:
            with open(inp, encoding="utf-8") as fh:
                data = json.load(fh)

        storage = SecureStorage()
        storage.store(platform, data)
        logger.info("Imported session for %s from %s", platform, inp)
        return {"success": True, "path": str(inp), "reason": ""}
    except Exception as exc:
        return {
            "success": False,
            "path": str(inp),
            "reason": str(exc),
        }


def delete_session(platform: str) -> dict:
    """Delete all stored session data for *platform*.

    Cleans up:
      - Secure storage (keyring / Fernet)
      - Plain-text cookie file (~/.cn-scraper-cookies/<platform>.json)
      - Chrome profile directory (for profile-based platforms like JD)
      - Export / cache files under ~/.cn-scraper-mcp/

    Returns:
        {"platform": str, "deleted_secure": bool, "deleted_cookie": bool,
         "deleted_profile": bool, "reason": str}
    """
    result: dict[str, Any] = {
        "platform": platform,
        "deleted_secure": False,
        "deleted_cookie": False,
        "deleted_profile": False,
        "reason": "",
    }

    # 1. Secure storage
    storage = SecureStorage()
    storage.delete(platform)
    result["deleted_secure"] = True

    # 2. Plain-text cookie file
    cookie_path = COOKIE_DIR / f"{platform}.json"
    if cookie_path.exists():
        try:
            cookie_path.unlink()
            result["deleted_cookie"] = True
            logger.info("Deleted plain-text cookie file: %s", cookie_path)
        except OSError as exc:
            result["reason"] += f"Cookie delete failed: {exc}; "

    # 3. Chrome profile (JD and other profile-based platforms)
    profile_dir: Path | None = None
    if platform == "jd":
        profile_dir = JD_PROFILE_DIR
    else:
        alt = Path.home() / f".cn_scraper_login_{platform}"
        if alt.exists():
            profile_dir = alt

    if profile_dir is not None and profile_dir.exists():
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            result["deleted_profile"] = True
            logger.info("Deleted Chrome profile: %s", profile_dir)
        except OSError as exc:
            result["reason"] += f"Profile delete failed: {exc}; "

    # 4. Clean up export / cache files
    if DEFAULT_STORAGE_DIR.exists():
        for pattern in (f"{platform}_export.enc", f"{platform}_export.json", f"{platform}.enc"):
            p = DEFAULT_STORAGE_DIR / pattern
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    if not result["reason"]:
        result["reason"] = ""

    logger.info(
        "Deleted session for platform=%s (secure=%s, cookie=%s, profile=%s)",
        platform,
        result["deleted_secure"],
        result["deleted_cookie"],
        result["deleted_profile"],
    )
    return result


def _check_plaintext_cookies() -> list[str]:
    """Check for plain-text cookie files and log a security warning.

    Called at server startup.  Returns list of platforms with plain-text
    cookie files.
    """
    if not COOKIE_DIR.exists():
        return []

    found: list[str] = []
    for entry in COOKIE_DIR.iterdir():
        if entry.suffix == ".json":
            found.append(entry.stem)

    if found:
        _warn_plaintext()

    return found


# ═══════════════════════════════════════════════════════════════
# Backend info (diagnostic)
# ═══════════════════════════════════════════════════════════════


def get_backend_info() -> dict:
    """Return diagnostic information about the active storage backend."""
    if _KEYRING_AVAILABLE:
        try:
            import keyring

            kr = keyring.get_keyring()
            return {
                "backend": "keyring",
                "keyring_class": kr.__class__.__name__,
                "keyring_priority": getattr(kr, "priority", None),
            }
        except Exception:
            pass

    if _FERNET_AVAILABLE:
        key_exists = DEFAULT_KEY_FILE.exists()
        storage_exists = DEFAULT_STORAGE_DIR.exists()
        return {
            "backend": "fernet",
            "key_file": str(DEFAULT_KEY_FILE),
            "key_exists": key_exists,
            "storage_dir": str(DEFAULT_STORAGE_DIR),
            "storage_exists": storage_exists,
        }

    return {"backend": "none", "reason": "Neither keyring nor cryptography installed"}


__all__ = [
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
]
