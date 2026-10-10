"""Field-level encryption for patient records (AES-256-GCM).

Every record is encrypted before it reaches the database, with the row's identity
(owner, patient, section, item) as associated data, so a ciphertext copied into another
row or account fails to decrypt. Keys are versioned for rotation:

    MEDICAL_DOCTOR_DATA_KEYS="k2:<base64 32 bytes>,k1:<old key>"   # first = active

Old keys stay listed until every record has been re-written with the active key.
"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DataKeyError(RuntimeError):
    pass


def generate_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def _decode(key: str) -> bytes:
    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    if len(raw) != 32:
        raise DataKeyError("Data keys must be 32 bytes, base64-encoded (use `medical-doctor-mcp gen-data-key`)")
    return raw


class Cipher:
    def __init__(self, keys: dict[str, bytes], active: str):
        if active not in keys:
            raise DataKeyError("active key id missing")
        self._keys = {kid: AESGCM(k) for kid, k in keys.items()}
        self.active = active

    @classmethod
    def from_spec(cls, spec: str) -> Cipher:
        keys: dict[str, bytes] = {}
        order: list[str] = []
        for i, item in enumerate(x.strip() for x in spec.split(",") if x.strip()):
            kid, sep, key = item.partition(":")
            if not sep:
                kid, key = f"k{i + 1}", item
            keys[kid] = _decode(key)
            order.append(kid)
        if not order:
            raise DataKeyError("no data keys configured")
        return cls(keys, order[0])

    @classmethod
    def ephemeral(cls) -> Cipher:
        return cls({"tmp": secrets.token_bytes(32)}, "tmp")

    def encrypt(self, plaintext: bytes, aad: bytes) -> str:
        nonce = os.urandom(12)
        ct = self._keys[self.active].encrypt(nonce, plaintext, aad)
        return f"{self.active}:{base64.b64encode(nonce + ct).decode()}"

    def decrypt(self, token: str, aad: bytes) -> bytes:
        kid, _, body = token.partition(":")
        if kid not in self._keys:
            raise DataKeyError(f"record encrypted with unknown key id {kid!r}")
        raw = base64.b64decode(body)
        return self._keys[kid].decrypt(raw[:12], raw[12:], aad)


def local_key_file(path: Path) -> str:
    """Single-user local mode: create (once) and reuse a key file readable only by the user."""
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = f"local:{generate_key()}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    return key
