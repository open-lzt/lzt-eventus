"""Fernet-backed encrypt/decrypt-at-rest primitive with a key-rotation seam."""

from __future__ import annotations

from secret_box.box import DecryptionFailed, KeyMissing, SecretBox, SecretBoxError

__all__ = ["DecryptionFailed", "KeyMissing", "SecretBox", "SecretBoxError"]
