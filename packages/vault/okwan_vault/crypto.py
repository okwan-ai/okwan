"""AES-GCM sealing.

Authenticated encryption with associated data: the AAD binds a ciphertext
to its context, so a credential sealed for tenant A and connector stripe
cannot be replayed as tenant B's, even by someone with database write
access. That is the property that makes row-level tampering detectable
rather than silent.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32


def new_key() -> bytes:
    return os.urandom(KEY_BYTES)


def seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """nonce || ciphertext || tag."""
    nonce = os.urandom(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(key: bytes, sealed: bytes, aad: bytes = b"") -> bytes:
    if len(sealed) <= NONCE_BYTES:
        raise ValueError("ciphertext too short")
    nonce, body = sealed[:NONCE_BYTES], sealed[NONCE_BYTES:]
    return AESGCM(key).decrypt(nonce, body, aad)
