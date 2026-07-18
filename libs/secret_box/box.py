"""Fernet encrypt/decrypt wrapper with a key-rotation seam.

A self-hosted, symmetric-at-rest primitive (no external KMS) — the raw key comes
from the host via DI (e.g. `EngineConfig.token_enc_key`); this module owns no
config of its own. Extracted on sight (`~/.claude/rules/patterns.md`
extract-on-sight list: crypto/HMAC primitives) so any second at-rest secret
(not just the lzt.market token) reuses it rather than re-rolling Fernet calls.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class SecretBoxError(RuntimeError):
    """Root of the secret_box error tree — always fail loud, never store plaintext."""


class KeyMissing(SecretBoxError):
    def __init__(self) -> None:
        super().__init__("secret_box: encryption key is empty")


class DecryptionFailed(SecretBoxError):
    def __init__(self) -> None:
        super().__init__("secret_box: ciphertext did not decrypt under any known key")


def _derive_fernet_key(raw_key: str) -> bytes:
    """Any-length secret -> a valid 32-byte urlsafe-base64 Fernet key, deterministically."""
    return base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode()).digest())


class SecretBox:
    """Encrypts under one active key; decrypts under the active key or any retired one.

    `rotate()` returns a new `SecretBox` with a fresh active key — old ciphertext
    keeps decrypting via the retired-key list until the host re-encrypts it at its
    own pace (no forced re-encryption sweep here; that's a host-owned migration).
    """

    def __init__(self, key: str, *, retired_keys: tuple[str, ...] = ()) -> None:
        if not key:
            raise KeyMissing()
        self._active_key = key
        self._retired_keys = retired_keys
        self._active = Fernet(_derive_fernet_key(key))
        self._readers = [self._active, *(Fernet(_derive_fernet_key(k)) for k in retired_keys)]

    def encrypt(self, plaintext: str) -> bytes:
        return self._active.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        for fernet in self._readers:
            try:
                return fernet.decrypt(ciphertext).decode()
            except InvalidToken:
                continue
        raise DecryptionFailed()

    def rotate(self, new_key: str) -> SecretBox:
        """Return a new box with `new_key` active; ciphertext under the old key still reads."""
        if not new_key:
            raise KeyMissing()
        return SecretBox(new_key, retired_keys=(self._active_key, *self._retired_keys))
