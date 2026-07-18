from __future__ import annotations

import pytest

from secret_box import DecryptionFailed, KeyMissing, SecretBox


def test_encrypt_decrypt_round_trip_is_identity() -> None:
    box = SecretBox("correct-horse-battery-staple")

    ciphertext = box.encrypt("super-secret-token")

    assert box.decrypt(ciphertext) == "super-secret-token"
    assert ciphertext != b"super-secret-token"


def test_decrypt_fails_under_wrong_key() -> None:
    box = SecretBox("key-one")
    other = SecretBox("key-two")

    ciphertext = box.encrypt("payload")

    with pytest.raises(DecryptionFailed):
        other.decrypt(ciphertext)


def test_empty_key_fails_loud_at_construction() -> None:
    with pytest.raises(KeyMissing):
        SecretBox("")


def test_rotate_keeps_old_ciphertext_readable() -> None:
    box = SecretBox("old-key")
    ciphertext = box.encrypt("payload")

    rotated = box.rotate("new-key")

    assert rotated.decrypt(ciphertext) == "payload"
    assert rotated.encrypt("payload") != ciphertext  # new encryptions use the new key


def test_rotate_to_empty_key_fails_loud() -> None:
    box = SecretBox("old-key")

    with pytest.raises(KeyMissing):
        box.rotate("")
