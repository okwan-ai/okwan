"""Master key providers.

Envelope encryption: each credential is sealed with its own data key, and
the data key is sealed by a master key. Rotating the master re-wraps data
keys rather than re-encrypting every secret, and the master itself can
live somewhere the application cannot read.

The provider is an interface so local development works without a cloud
account while production keeps the master in a KMS that never hands it
over. Swapping is configuration, not a rewrite.
"""
from __future__ import annotations

import base64
import os
from typing import Protocol


class MasterKeyProvider(Protocol):
    """Wraps and unwraps data keys. Never exposes the master itself."""

    @property
    def key_id(self) -> str:
        """Identifies which master sealed a given payload."""
        ...

    def wrap(self, data_key: bytes) -> bytes: ...

    def unwrap(self, wrapped: bytes) -> bytes: ...


class EnvMasterKey:
    """Master key from an environment variable. Development only.

    Anyone who can read the process environment can decrypt every stored
    credential. That is acceptable on a laptop and not acceptable in
    production, which is the entire reason this is an interface.
    """

    ENV_VAR = "OKWAN_VAULT_MASTER_KEY"

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            raw = os.environ.get(self.ENV_VAR, "")
            if not raw:
                raise RuntimeError(
                    f"{self.ENV_VAR} is not set. Generate one with "
                    "`python -m okwan_vault keygen`."
                )
            key = base64.urlsafe_b64decode(raw)
        if len(key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._key = key

    @property
    def key_id(self) -> str:
        return "env:v1"

    def wrap(self, data_key: bytes) -> bytes:
        from .crypto import seal

        return seal(self._key, data_key, aad=b"okwan-data-key")

    def unwrap(self, wrapped: bytes) -> bytes:
        from .crypto import open_sealed

        return open_sealed(self._key, wrapped, aad=b"okwan-data-key")


class KmsMasterKey:
    """Master key held in a cloud KMS. The key never reaches this process.

    Constructed lazily so the google-cloud-kms dependency is only needed
    where it is actually used.
    """

    def __init__(self, resource_name: str) -> None:
        self._name = resource_name
        self._client = None

    @property
    def key_id(self) -> str:
        return f"kms:{self._name}"

    def _kms(self):
        if self._client is None:
            from google.cloud import kms  # type: ignore[import-not-found]

            self._client = kms.KeyManagementServiceClient()
        return self._client

    def wrap(self, data_key: bytes) -> bytes:
        return self._kms().encrypt(
            request={"name": self._name, "plaintext": data_key}
        ).ciphertext

    def unwrap(self, wrapped: bytes) -> bytes:
        return self._kms().decrypt(
            request={"name": self._name, "ciphertext": wrapped}
        ).plaintext


def from_env() -> MasterKeyProvider:
    """Pick a provider from configuration. KMS wins when present."""
    kms_name = os.environ.get("OKWAN_VAULT_KMS_KEY", "")
    if kms_name:
        return KmsMasterKey(kms_name)
    return EnvMasterKey()
