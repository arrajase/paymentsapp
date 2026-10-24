"""Field-level encryption for restricted data (plan 7.3): AES-256-GCM with a key ID, so keys can rotate.

Stored form: "<key_id>:<base64(nonce + ciphertext + tag)>". The column and row are bound in as associated data,
so a ciphertext copied into another column or row fails to decrypt.

In development the key comes from FIELD_ENCRYPTION_KEYS in backend/.env. Production replaces this with envelope
encryption through a KMS (plan 7.3), behind the same encrypt/decrypt calls.
"""

import base64
import os
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from paymentsapp.core.config import get_settings

_NONCE_BYTES = 12


class DecryptionError(Exception):
    """Wrong key, tampered data, or data moved to another column or row."""


def generate_key() -> str:
    """A new random 256-bit key, base64-encoded for FIELD_ENCRYPTION_KEYS."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode()


class FieldCipher:
    def __init__(self, keys: dict[str, bytes], active_key_id: str):
        if active_key_id not in keys:
            raise ValueError("the active key ID has no key")
        for key_id, key in keys.items():
            if len(key) != 32:
                raise ValueError(f"key {key_id!r} must be 32 bytes")
            if ":" in key_id:
                raise ValueError("key IDs cannot contain ':'")
        self._keys = keys
        self._active = active_key_id

    @classmethod
    def from_setting(cls, value: str) -> "FieldCipher":
        """Parse "id1=base64key,id2=base64key". The first key encrypts; all of them decrypt (for rotation)."""
        keys: dict[str, bytes] = {}
        for item in value.split(","):
            key_id, sep, encoded = item.strip().partition("=")
            if not sep or not key_id:
                raise ValueError("FIELD_ENCRYPTION_KEYS must look like 'k1=<base64 key>[,k0=<old key>]'")
            keys[key_id] = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        return cls(keys, active_key_id=next(iter(keys)))

    def encrypt(self, plaintext: str, context: str) -> str:
        """`context` names where the value lives, e.g. "admin_users.totp_secret:<id>". Use the same one to decrypt."""
        nonce = os.urandom(_NONCE_BYTES)
        sealed = AESGCM(self._keys[self._active]).encrypt(nonce, plaintext.encode(), context.encode())
        return f"{self._active}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"

    def decrypt(self, stored: str, context: str) -> str:
        key_id, sep, encoded = stored.partition(":")
        key = self._keys.get(key_id)
        if not sep or key is None:
            raise DecryptionError("unknown key ID")
        raw = base64.urlsafe_b64decode(encoded)
        try:
            return AESGCM(key).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], context.encode()).decode()
        except InvalidTag:
            raise DecryptionError("ciphertext does not verify") from None

    def needs_rotation(self, stored: str) -> bool:
        return not stored.startswith(f"{self._active}:")


@lru_cache
def get_cipher() -> FieldCipher:
    keys = get_settings().field_encryption_keys
    if keys is None:
        raise RuntimeError("FIELD_ENCRYPTION_KEYS is not set in backend/.env")
    return FieldCipher.from_setting(keys.get_secret_value())
