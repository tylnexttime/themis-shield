"""Key protectors for the vault's user wrapping key, and how to choose one.

- ``DPAPIProtector`` (Windows): CurrentUser DPAPI, see :mod:`themis_shield.dpapi`.
- ``PassphraseProtector`` (any platform): scrypt (n=2**15, r=8, p=1) derives a
  256-bit key from a passphrase; AES-256-GCM wraps the value. The 16-byte salt
  and 12-byte nonce are stored with the wrapped key.
- ``NullProtector``: identity, **not protection**. Only usable when the
  configuration explicitly sets ``insecure_test_only = true``.

Off Windows there is no silent fallback: unless a passphrase protector is
configured, the vault refuses to start.
"""

from __future__ import annotations

import getpass
import os
import secrets
import sys
from threading import Lock
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .dpapi import DPAPIProtector, KeyProtector, NullProtector, ProtectionError

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
NONCE_BYTES = 12
_MAGIC = b"TSP1"
_AAD = b"themis-shield/passphrase-protector/v1"
DEFAULT_PASSPHRASE_ENV = "THEMIS_VAULT_PASSPHRASE"
MIN_PASSPHRASE_CHARS = 12


class PassphraseMismatch(ProtectionError):
    """A passphrase-protected key exists but this passphrase does not unwrap it."""


class PassphraseProtector:
    """Wrap bytes under a passphrase-derived key (scrypt + AES-256-GCM).

    Format: ``b"TSP1" | salt(16) | nonce(12) | ciphertext+tag``. A fresh salt
    and nonce are used for every ``protect`` call. Derived keys are kept in
    memory per salt for the life of the process (the passphrase itself is
    held for the same lifetime), so repeated unwraps do not re-run scrypt.
    """

    def __init__(self, passphrase: str | bytes):
        raw = passphrase.encode("utf-8") if isinstance(passphrase, str) else bytes(passphrase)
        if len(raw) < MIN_PASSPHRASE_CHARS:
            raise ProtectionError(
                f"vault passphrase must be at least {MIN_PASSPHRASE_CHARS} characters"
            )
        self._passphrase = raw
        self._keys: dict[bytes, bytes] = {}
        self._lock = Lock()

    def _key(self, salt: bytes) -> bytes:
        with self._lock:
            key = self._keys.get(salt)
            if key is None:
                key = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
                    self._passphrase
                )
                self._keys[salt] = key
            return key

    def protect(self, value: bytes) -> bytes:
        salt = secrets.token_bytes(SALT_BYTES)
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = AESGCM(self._key(salt)).encrypt(nonce, bytes(value), _AAD)
        return _MAGIC + salt + nonce + sealed

    def unprotect(self, value: bytes) -> bytes:
        value = bytes(value)
        header = len(_MAGIC) + SALT_BYTES + NONCE_BYTES
        if len(value) <= header or not value.startswith(_MAGIC):
            raise ProtectionError("not a passphrase-protected key")
        salt = value[len(_MAGIC):len(_MAGIC) + SALT_BYTES]
        nonce = value[len(_MAGIC) + SALT_BYTES:header]
        try:
            return AESGCM(self._key(salt)).decrypt(nonce, value[header:], _AAD)
        except InvalidTag:
            raise PassphraseMismatch("vault passphrase does not match") from None

    @classmethod
    def from_environment(
        cls, env_name: str = DEFAULT_PASSPHRASE_ENV, *,
        environ: Mapping[str, str] | None = None, prompt: bool = True,
    ) -> PassphraseProtector:
        """Read the passphrase from ``env_name``; else prompt on an interactive terminal."""
        source = os.environ if environ is None else environ
        value = source.get(env_name)
        if value:
            return cls(value)
        if prompt and sys.stdin is not None and sys.stdin.isatty():
            return cls(getpass.getpass("themis-shield vault passphrase: "))
        raise ProtectionError(
            f"vault passphrase unavailable: set {env_name} or run interactively"
        )


PROTECTOR_KINDS = ("auto", "dpapi", "passphrase", "null")


def build_protector(config: Mapping[str, Any] | None = None, *, platform: str | None = None) -> KeyProtector:
    """Choose the vault protector from the ``[vault]`` configuration table.

    ``protector = "auto"`` (default) means DPAPI on Windows and refusal
    elsewhere. ``"passphrase"`` reads ``passphrase_env`` (default
    ``THEMIS_VAULT_PASSPHRASE``) or prompts. ``"null"`` requires
    ``insecure_test_only = true`` and is never chosen implicitly.
    """
    config = dict(config or {})
    unknown = set(config) - {"protector", "passphrase_env", "insecure_test_only", "path"}
    if unknown:
        raise ProtectionError("unknown [vault] settings")
    kind = config.get("protector", "auto")
    insecure = config.get("insecure_test_only", False)
    if kind not in PROTECTOR_KINDS or type(insecure) is not bool:
        raise ProtectionError("invalid [vault] protector setting")
    platform = os.name if platform is None else platform
    if kind == "null":
        if insecure is not True:
            raise ProtectionError("the null protector requires insecure_test_only = true")
        return NullProtector()
    if kind == "passphrase":
        return PassphraseProtector.from_environment(
            config.get("passphrase_env") or DEFAULT_PASSPHRASE_ENV
        )
    if kind == "dpapi" or platform == "nt":
        if platform != "nt":
            raise ProtectionError("DPAPI is only available on Windows")
        return DPAPIProtector()
    raise ProtectionError(
        "no vault protector configured for this platform: set [vault] "
        'protector = "passphrase" and provide the passphrase'
    )
